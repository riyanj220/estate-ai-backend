"""Helpers shared by more than one identity service module."""

import hashlib

from django.db import connection
from django.db.models import Value
from django.db.models.functions import Lower
from django.utils import timezone

from apps.identity.models import (
    Account,
    EmailChangeToken,
    Session,
)
from apps.identity.selectors import (
    AccountView,
    get_account,
    parse_id,
)
from apps.identity.state import AccountState
from apps.kernel import audit
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError

EMAIL_CONSTRAINT = "identity_account_email_ci_uniq"


def _require_system(actor: Actor) -> None:
    require_signed_in(actor)
    if actor.kind is not ActorKind.SYSTEM:
        raise DomainError("not_found")


def _email_taken(email: str) -> bool:
    return (
        Account.objects.annotate(lowered=Lower("email"))
        .filter(lowered=Lower(Value(email)))
        .exists()
    )


def _locked(account_id) -> Account:
    """The account row, locked. Unknown ids and deactivated accounts are `not_found`."""
    key = parse_id(account_id)
    account = Account.objects.select_for_update().filter(id=key).first() if key else None
    if account is None or account.state is AccountState.DEACTIVATED:
        raise DomainError("not_found")
    return account


def _void_email_change_links(account: Account, now) -> None:
    """A password change, reset or deactivation kills every unused email-change link, so a link
    mailed before it (possibly by someone who held a session) cannot be used after."""
    EmailChangeToken.objects.filter(account=account, used_at__isnull=True).update(used_at=now)


def _view(account_id) -> AccountView:
    view = get_account(account_id)
    if view is None:
        raise RuntimeError("account vanished inside its own transaction")
    return view


def _lock_key(key: str) -> None:
    """Serialise sign-in attempts per throttle key for the rest of the transaction."""
    number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [number])


def _end_open_sessions(
    account_id, reason: str, except_session_id=None, ended_before=None, now=None
) -> int:
    sessions_ = Session.objects.filter(account_id=account_id, ended_at__isnull=True)
    if except_session_id is not None:
        sessions_ = sessions_.exclude(id=except_session_id)
    if ended_before is not None:
        sessions_ = sessions_.filter(created_at__lte=ended_before)
    return sessions_.update(ended_at=now or timezone.now(), ended_reason=reason)


def _record_verified(actor: Actor, account: Account) -> None:
    if account.email_verified_at is None:
        account.email_verified_at = timezone.now()
        account.save(update_fields=["email_verified_at", "updated_at"])
        audit.record(actor, "account.email_verified", "account", account.id)


def _require_owner_actor(actor: Actor) -> None:
    """Signed in with an account of its own; administrators and system actors are `forbidden`."""
    require_signed_in(actor)
    if actor.account_id is None or actor.is_admin:
        raise DomainError("forbidden")
