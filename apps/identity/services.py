"""Write path for identity. Only this module writes the account table.

Every state change and its audit record share one transaction.
"""

from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from apps.identity.events import emit_session_ended
from apps.identity.models import Account, AccountType
from apps.identity.selectors import AccountView, get_account, parse_id
from apps.identity.state import AccountState
from apps.kernel import audit
from apps.kernel.actor import Actor, ActorKind, require_admin, require_signed_in
from apps.kernel.errors import DomainError
from apps.kernel.privacy.patterns import (
    EMAIL_ADDRESS_SHAPE,
    LONG_DIGIT_RUN,
    reject_contact_details,
)

EMAIL_MAX = 254
PASSWORD_MAX = 1024
EMAIL_CONSTRAINT = "identity_account_email_ci_uniq"


def _require_system(actor: Actor) -> None:
    require_signed_in(actor)
    if actor.kind is not ActorKind.SYSTEM:
        raise DomainError("not_found")


def _clean_email(email) -> str:
    if not isinstance(email, str):
        raise DomainError("validation_error")
    email = email.strip()
    if (
        len(email) > EMAIL_MAX
        or not email.isprintable()
        or not EMAIL_ADDRESS_SHAPE.fullmatch(email)
    ):
        raise DomainError("validation_error")
    if "" in email.rpartition("@")[2].split("."):  # empty domain label
        raise DomainError("validation_error")
    return email


def _clean_reason(reason) -> str:
    if not isinstance(reason, str):
        raise DomainError("validation_error")
    reason = reason.strip()
    if (
        not 1 <= len(reason) <= audit.REASON_MAX
        or not reason.isprintable()
        or LONG_DIGIT_RUN.search(reason)
        or reject_contact_details(reason) is not None
    ):
        raise DomainError("validation_error")
    return reason


def _email_taken(email: str) -> bool:
    return Account.objects.annotate(lowered=Lower("email")).filter(lowered=email.lower()).exists()


def _locked(account_id) -> Account:
    """The account row, locked. Unknown ids and deactivated accounts are `not_found`."""
    key = parse_id(account_id)
    account = Account.objects.select_for_update().filter(id=key).first() if key else None
    if account is None or account.state is AccountState.DEACTIVATED:
        raise DomainError("not_found")
    return account


def _require_manageable(actor: Actor, account: Account) -> None:
    """Administrators never suspend, reactivate or deactivate themselves or other administrators."""
    if account.account_type == AccountType.ADMIN or account.id == actor.account_id:
        raise DomainError("validation_error")


def _view(account_id) -> AccountView:
    view = get_account(account_id)
    if view is None:
        raise RuntimeError("account vanished inside its own transaction")
    return view


def create_account(actor: Actor, email: str, password: str, account_type: str) -> AccountView:
    """Create an unverified account. Used by registration and administrator provisioning."""
    _require_system(actor)
    email = _clean_email(email)
    if not isinstance(password, str) or not password.strip() or len(password) > PASSWORD_MAX:
        raise DomainError("validation_error")
    if account_type not in {t.value for t in AccountType}:
        raise DomainError("validation_error")
    with transaction.atomic():
        if _email_taken(email):
            raise DomainError("validation_error")
        try:
            with transaction.atomic():
                account = Account.objects.create(
                    email=email, password=make_password(password), account_type=account_type
                )
        except IntegrityError as exc:
            diag = getattr(exc.__cause__, "diag", None)
            if getattr(diag, "constraint_name", None) != EMAIL_CONSTRAINT:
                raise
            raise DomainError("validation_error") from None  # lost a race on the email
        audit.record(
            actor, "account.created", "account", account.id, {"account_type": account_type}
        )
        return _view(account.id)


def mark_email_verified(actor: Actor, account_id) -> AccountView:
    """Idempotent: a second call changes nothing and writes no second audit record."""
    _require_system(actor)
    with transaction.atomic():
        account = _locked(account_id)
        if account.email_verified_at is None:
            account.email_verified_at = timezone.now()
            account.save(update_fields=["email_verified_at", "updated_at"])
            audit.record(actor, "account.email_verified", "account", account.id)
        return _view(account.id)


def suspend(actor: Actor, account_id, reason: str) -> AccountView:
    """Suspend an account. The reason is kept on the row only, never in the audit record."""
    require_admin(actor)
    reason = _clean_reason(reason)
    with transaction.atomic():
        account = _locked(account_id)
        _require_manageable(actor, account)
        if account.suspended_at is not None:
            raise DomainError("validation_error")
        account.suspended_at = timezone.now()
        account.suspension_reason = reason
        account.save(update_fields=["suspended_at", "suspension_reason", "updated_at"])
        audit.record(actor, "account.suspended", "account", account.id)
        return _view(account.id)


def reactivate(actor: Actor, account_id) -> AccountView:
    """Clear the suspension only: back to Active if the email is verified, else Email-unverified."""
    require_admin(actor)
    with transaction.atomic():
        account = _locked(account_id)  # a deactivated account is `not_found`: never reactivated
        _require_manageable(actor, account)
        if account.suspended_at is None:
            raise DomainError("validation_error")
        account.suspended_at = None
        account.suspension_reason = ""
        account.save(update_fields=["suspended_at", "suspension_reason", "updated_at"])
        audit.record(actor, "account.reactivated", "account", account.id)
        return _view(account.id)


def deactivate(actor: Actor, account_id) -> AccountView:
    """Deactivate an account for good and end its sessions once the change commits."""
    require_admin(actor)
    with transaction.atomic():
        account = _locked(account_id)
        _require_manageable(actor, account)
        account.deactivated_at = timezone.now()
        account.save(update_fields=["deactivated_at", "updated_at"])
        audit.record(actor, "account.deactivated", "account", account.id)
        emit_session_ended(account.id, "deactivated")
        return _view(account.id)
