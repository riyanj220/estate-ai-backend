"""Password reset and password change (Story 1.18)."""

import hashlib

from django.contrib.auth.hashers import check_password, is_password_usable, make_password
from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone

from apps.identity import (
    notices,
    password_reset,
    passwords,
    verification,
)
from apps.identity.events import (
    emit_password_reset_requested,
    emit_session_ended,
)
from apps.identity.models import (
    Account,
    AccountType,
    PasswordResetToken,
    Session,
)
from apps.identity.passwords import PASSWORD_MAX
from apps.identity.selectors import (
    parse_id,
)
from apps.identity.services._common import (
    _end_open_sessions,
    _lock_key,
    _require_system,
    _void_email_change_links,
)
from apps.identity.state import AccountState
from apps.identity.validation import (
    check_email,
)
from apps.kernel import audit, notifications, ratelimit
from apps.kernel.actor import Actor, require_signed_in
from apps.kernel.errors import DomainError


class PasswordInvalid(DomainError):
    """A password field failed. `fields` maps field name to a stable error code."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("validation_error")
        self.fields = dict(fields)


def _eligible_for_reset(account: Account | None) -> bool:
    """Only an existing, non-deactivated, non-administrator account with a usable password can be
    reset: an administrator's forgotten password is recovered by the provisioning command only."""
    return (
        account is not None
        and account.account_type != AccountType.ADMIN
        and account.state is not AccountState.DEACTIVATED
        and is_password_usable(account.password)
    )


def request_password_reset(actor: Actor, email) -> None:
    """Start a reset if the address belongs to an eligible account; reveal nothing.

    Returns nothing in every case. Every well-formed-or-not string takes the same kind of
    response-path work: one lookup and one counter write in the shared `password_reset_email`
    scope (5 an hour, silent), keyed by the account for an eligible one and by a hash of the
    normalised address for everything else. Only an eligible account under its cap emits
    `account.password_reset_requested`; the token and the email are made after commit by the
    handler. The remaining difference (the after-commit handler and SMTP time) is not visible in
    the response.
    """
    if not isinstance(email, str) or "\x00" in email:
        return
    cleaned, code = check_email(email)
    with transaction.atomic():
        existing = None
        if not code:
            existing = (
                Account.objects.annotate(lowered=Lower("email"))
                .filter(lowered=cleaned.lower())
                .first()
            )
        if _eligible_for_reset(existing):
            key = ratelimit.derive_key("account", str(existing.id))
        else:
            address = hashlib.sha256((cleaned or email).strip().lower().encode()).hexdigest()
            key = ratelimit.derive_key("address", address)
        decision = ratelimit.hit("password_reset_email", key)
        if _eligible_for_reset(existing) and decision.allowed:
            emit_password_reset_requested(existing.id)


def issue_password_reset_token(actor: Actor, account_id) -> tuple[str, str] | None:
    """Issue a fresh reset token and supersede the account's earlier unused ones.

    Returns `(email, token)` for the caller to mail inside the same transaction, or None for an
    unknown, Deactivated or unusable-password account. The plain token is never stored.
    """
    _require_system(actor)
    key = parse_id(account_id)
    with transaction.atomic():
        account = Account.objects.select_for_update().filter(id=key).first() if key else None
        if not _eligible_for_reset(account):
            return None
        now = timezone.now()
        PasswordResetToken.objects.filter(account=account, used_at__isnull=True).update(used_at=now)
        token = verification.new_token()
        PasswordResetToken.objects.create(
            account=account,
            token_hash=verification.hash_token(token),
            created_at=now,
            expires_at=now + password_reset.TOKEN_TTL,
        )
        return account.email, token


def confirm_password_reset(actor: Actor, token, password) -> None:
    """Consume a reset link and set a new password, ending every session, in one transaction.

    Raises `link_invalid`, `link_used` or `link_expired` first (so a field error shows only for a
    valid link), then `PasswordInvalid` for a password the policy refuses; a refused password
    leaves the token usable. The password is hashed on success only. The token is never echoed.
    """
    if not verification.well_formed(token):
        raise DomainError("link_invalid")
    token_hash = verification.hash_token(token)
    with transaction.atomic():
        found = PasswordResetToken.objects.filter(token_hash=token_hash).first()
        if found is None:
            raise DomainError("link_invalid")
        # Lock order is account then token, as in `issue_password_reset_token` and email
        # confirmation. A purge or cascade after the lookup makes the link invalid.
        account = Account.objects.select_for_update().filter(id=found.account_id).first()
        row = PasswordResetToken.objects.select_for_update().filter(id=found.id).first()
        if (
            account is None
            or row is None
            or account.state is AccountState.DEACTIVATED
            or account.account_type == AccountType.ADMIN
        ):
            raise DomainError("link_invalid")
        now = timezone.now()
        if row.used_at is not None:
            raise DomainError("link_used")
        if row.expires_at <= now:
            raise DomainError("link_expired")
        code = passwords.password_error(password, account.email)
        if code:
            raise PasswordInvalid({"password": code})
        account.password = make_password(password)
        account.save(update_fields=["password", "updated_at"])
        row.used_at = now
        row.save(update_fields=["used_at"])
        PasswordResetToken.objects.filter(account=account, used_at__isnull=True).update(used_at=now)
        _void_email_change_links(account, now)
        _end_open_sessions(account.id, "password_changed", now=now)
        audit.record(
            Actor.system(actor.request_id), "account.password_reset", "account", account.id
        )
        emit_session_ended(account.id, "password_changed", ended_before=now)
        notices.send_password_reset_done(account.email)
        notifications.notify(account.id, "account.password_changed", {})  # last: see docs


def _change_attempt(key: str, actor: Actor, session_id, current: str, new: str, now) -> bool:
    """One change attempt under the per-key lock. False means the current password was wrong or
    the throttle is full; that has been counted and committed. Other failures raise."""
    with transaction.atomic():
        _lock_key(key)
        if ratelimit.peek("password_change_failed", key, now):
            return False
        account = Account.objects.select_for_update().filter(id=actor.account_id).first()
        open_session = (
            account is not None
            and session_id is not None
            and Session.objects.filter(
                id=session_id,
                account_id=account.id,
                ended_at__isnull=True,
                expires_at__gt=now,
            ).exists()
        )
        if not open_session or account.state is AccountState.DEACTIVATED:
            raise DomainError("unauthenticated")
        usable = is_password_usable(account.password)
        matches = (
            len(current) <= PASSWORD_MAX
            and check_password(current, account.password if usable else passwords.dummy_hash())
            and usable
        )
        if not matches:
            ratelimit.hit("password_change_failed", key, now)
            return False
        code = passwords.policy_error(account.account_type, new, account.email)
        if code:
            raise PasswordInvalid({"new_password": code})
        if new == current:
            raise PasswordInvalid({"new_password": "password_same_as_current"})
        account.password = make_password(new)
        account.save(update_fields=["password", "updated_at"])
        now = timezone.now()  # after the locks: later sessions are outside this change's reach
        _end_open_sessions(account.id, "password_changed", session_id, now=now)
        PasswordResetToken.objects.filter(account=account, used_at__isnull=True).update(
            used_at=now  # an outstanding reset link must not outlive the change
        )
        _void_email_change_links(account, now)
        audit.record(actor, "account.password_changed", "account", account.id)
        emit_session_ended(account.id, "password_changed", session_id, ended_before=now)
        notices.send_password_changed(account.email)
        notifications.notify(account.id, "account.password_changed", {})  # last: see docs
        return True


def change_password(actor: Actor, session_id, current_password, new_password) -> None:
    """Change the signed-in account's password and end every other session.

    `session_id` is the caller's own session, which stays signed in. A wrong current password is
    `PasswordInvalid({"current_password": "incorrect"})`; after 5 wrong attempts in 15 minutes the
    same error is returned without checking (never a 429), counting failures only, one attempt at
    a time per account (the 1.17 advisory-lock pattern). The new password follows the
    registration policy and must differ from the current one.
    """
    require_signed_in(actor)
    if actor.account_id is None:
        raise DomainError("unauthenticated")
    keep = parse_id(session_id)
    if not isinstance(current_password, str) or not isinstance(new_password, str):
        raise DomainError("validation_error")
    key = ratelimit.derive_key("password_change_failed", str(actor.account_id))
    if not _change_attempt(key, actor, keep, current_password, new_password, timezone.now()):
        raise PasswordInvalid({"current_password": "incorrect"})
