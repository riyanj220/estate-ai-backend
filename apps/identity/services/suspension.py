"""Administrator suspension and reactivation (Story 2.21, FR-59).

Only the account row changes: no Listing status moves and no session ends. Everything else
follows from the derived state, which is read on every request (`docs/account-states.md`).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.identity.events import emit_suspended
from apps.identity.models import Account, AccountType
from apps.identity.selectors import parse_id
from apps.identity.services.profile import FieldsInvalid
from apps.identity.state import AccountState
from apps.kernel import audit, notifications
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError
from apps.kernel.privacy.patterns import LONG_DIGIT_RUN, reject_contact_details

REASON_MIN = 5
REASON_MAX = 500


@dataclass(frozen=True)
class SuspensionView:
    """The state of an account after a change. The reason is for administrators and the account."""

    id: uuid.UUID
    type: str
    state: AccountState
    suspension_reason: str
    suspended_at: datetime | None


def _reason_error(reason) -> str | None:
    if reason is None:
        return "required"
    if not isinstance(reason, str):
        return "invalid"
    reason = reason.strip()
    if len(reason) < REASON_MIN:
        return "too_short"
    if len(reason) > REASON_MAX:
        return "too_long"
    if not reason.isprintable():
        return "invalid"
    if LONG_DIGIT_RUN.search(reason) or reject_contact_details(reason) is not None:
        return "contains_contact_details"
    return None


def _clean_reason(reason) -> str:
    if code := _reason_error(reason):
        raise FieldsInvalid({"reason": code})
    return reason.strip()


def _locked_target(account_id) -> Account:
    """The Individual or Agency account, locked. Unknown ids and administrators are `not_found`."""
    key = parse_id(account_id)
    account = Account.objects.select_for_update().filter(id=key).first() if key else None
    if account is None or account.account_type == AccountType.ADMIN:
        raise DomainError("not_found")
    return account


def _view(account: Account) -> SuspensionView:
    return SuspensionView(
        account.id,
        account.account_type,
        account.state,
        account.suspension_reason,
        account.suspended_at,
    )


def suspend_account(actor: Actor, account_id, reason) -> SuspensionView:
    """Suspend an Active or Email-unverified account; Suspended or Deactivated is `conflict`.

    The reason (5 to 500 characters after trimming) is kept on the row only."""
    require_admin(actor)
    reason = _clean_reason(reason)
    with transaction.atomic():
        account = _locked_target(account_id)
        if account.state in (AccountState.SUSPENDED, AccountState.DEACTIVATED):
            raise DomainError("conflict")
        account.suspended_at = timezone.now()
        account.suspension_reason = reason
        account.reactivation_reason = ""
        account.save(
            update_fields=[
                "suspended_at",
                "suspension_reason",
                "reactivation_reason",
                "updated_at",
            ]
        )
        audit.record(actor, "account.suspended", "account", account.id)
        emit_suspended(account.id)
        notifications.notify(account.id, "account.suspended", {})  # last: see docs
        return _view(account)


def reactivate_account(actor: Actor, account_id, reason) -> SuspensionView:
    """Clear the suspension only: Active if the email is verified, else Email-unverified.

    Anything but a Suspended account is `conflict`."""
    require_admin(actor)
    reason = _clean_reason(reason)
    with transaction.atomic():
        account = _locked_target(account_id)
        if account.state is not AccountState.SUSPENDED:
            raise DomainError("conflict")
        account.suspended_at = None
        account.suspension_reason = ""
        account.reactivation_reason = reason
        account.save(
            update_fields=[
                "suspended_at",
                "suspension_reason",
                "reactivation_reason",
                "updated_at",
            ]
        )
        audit.record(actor, "account.reactivated", "account", account.id)
        notifications.notify(account.id, "account.reactivated", {})  # last: see docs
        return _view(account)
