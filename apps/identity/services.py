"""Write path for identity. Only this module writes the account table.

Every state change and its audit record share one transaction.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.contrib.auth.hashers import check_password, is_password_usable, make_password
from django.db import IntegrityError, connection, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from apps.identity import notices, password_reset, passwords, sessions, verification
from apps.identity.events import (
    SESSION_END_REASONS,
    emit_password_reset_requested,
    emit_registered,
    emit_session_ended,
    emit_verification_requested,
)
from apps.identity.models import (
    Account,
    AccountType,
    AgencyProfile,
    EmailVerificationToken,
    IndividualProfile,
    PasswordResetToken,
    Session,
)
from apps.identity.passwords import PASSWORD_MAX
from apps.identity.selectors import (
    AccountView,
    ActorSummary,
    city_is_active,
    get_account,
    parse_id,
    summarise,
)
from apps.identity.state import AccountState
from apps.identity.validation import (
    AgencyDetails,
    check_email,
    validate_agency,
    validate_individual,
)
from apps.kernel import audit, ratelimit
from apps.kernel.actor import Actor, ActorKind, require_admin, require_signed_in
from apps.kernel.errors import DomainError
from apps.kernel.privacy.patterns import LONG_DIGIT_RUN, reject_contact_details

EMAIL_CONSTRAINT = "identity_account_email_ci_uniq"


def _require_system(actor: Actor) -> None:
    require_signed_in(actor)
    if actor.kind is not ActorKind.SYSTEM:
        raise DomainError("not_found")


def _clean_email(email) -> str:
    email, code = check_email(email)
    if code:
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


def _insert_account(actor: Actor, email: str, hashed: str, account_type: str) -> Account | None:
    """Insert the account and its audit record; None when the email is already registered.

    Runs in the caller's transaction. A lost race on the unique email counts as taken.
    """
    if _email_taken(email):
        return None
    try:
        with transaction.atomic():
            account = Account.objects.create(
                email=email, password=hashed, account_type=account_type
            )
    except IntegrityError as exc:
        diag = getattr(exc.__cause__, "diag", None)
        if getattr(diag, "constraint_name", None) != EMAIL_CONSTRAINT:
            raise
        return None
    audit.record(actor, "account.created", "account", account.id, {"account_type": account_type})
    return account


def create_account(actor: Actor, email: str, password: str, account_type: str) -> AccountView:
    """Create an unverified account. Used by administrator provisioning."""
    _require_system(actor)
    email = _clean_email(email)
    if not isinstance(password, str) or not password.strip() or len(password) > PASSWORD_MAX:
        raise DomainError("validation_error")
    if account_type not in {t.value for t in AccountType}:
        raise DomainError("validation_error")
    with transaction.atomic():
        account = _insert_account(actor, email, make_password(password), account_type)
        if account is None:
            raise DomainError("validation_error")
        return _view(account.id)


class RegistrationInvalid(DomainError):
    """Registration fields failed validation. `fields` maps field name to a stable error code."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("validation_error")
        self.fields = dict(fields)


def _notify_existing(typed_email: str) -> None:
    """Tell the existing account's owner, at most 5 times an hour per account, silently.

    An unverified account gets a new verification request (Story 1.16 sends it); any other gets
    the sign-in notice, addressed to the stored email, not the typed case variant.
    """
    existing = (
        Account.objects.annotate(lowered=Lower("email")).filter(lowered=typed_email.lower()).first()
    )
    if existing is None:
        return
    key = ratelimit.derive_key("account", str(existing.id))
    if not ratelimit.hit("verification_email", key).allowed:
        return
    if existing.email_verified_at is None:
        emit_verification_requested(existing.id)
    else:
        notices.send_already_registered(existing.email)


def _register(
    actor: Actor,
    email: str,
    password: str,
    account_type: str,
    create_profile: Callable[[Account], None],
) -> None:
    """The part every registration shares, after validation.

    The password is hashed on both paths so timing does not reveal whether the email exists; an
    existing email takes the shared notification path and nothing is created.
    """
    hashed = make_password(password)
    with transaction.atomic():
        account = _insert_account(actor, email, hashed, account_type)
        if account is None:
            _notify_existing(email)
            return
        create_profile(account)
        emit_registered(account.id)


def register_individual(actor: Actor, full_name, email, password, phone) -> None:
    """Register an Individual account in the Email-unverified state.

    Returns nothing, whether or not the email is already registered, so the caller cannot tell
    (an existing address is sent an "already registered" notice instead). The password is hashed
    on both paths so timing does not reveal existence. Only a Visitor may register.
    """
    if not actor.is_visitor:
        raise DomainError("forbidden")
    details, errors = validate_individual(full_name, email, password, phone)
    if details is None:
        raise RegistrationInvalid(errors)
    _register(
        actor,
        details.email,
        details.password,
        AccountType.INDIVIDUAL.value,
        lambda account: IndividualProfile.objects.create(
            account=account, full_name=details.full_name, phone=details.phone
        ),
    )


def _create_agency_profile(account: Account, details: AgencyDetails) -> None:
    """Re-check the city inside the transaction: a deactivation since validation rolls it back."""
    if not city_is_active(details.city_id):
        raise RegistrationInvalid({"city_id": "invalid_city"})
    AgencyProfile.objects.create(
        account=account,
        agency_name=details.agency_name,
        business_phone=details.phone,
        office_address=details.office_address,
        city_id=details.city_id,
    )


def register_agency(
    actor: Actor, agency_name, email, password, phone, office_address, city_id
) -> None:
    """Register an Agency account (verification status "unverified") in the Email-unverified state.

    Same outcome whether or not the email is already registered, same timing guard and same
    verification flow as `register_individual`. The city must be an active City reference row.
    The business phone and office address are private and appear in no event, audit or log.
    """
    if not actor.is_visitor:
        raise DomainError("forbidden")
    details, errors = validate_agency(
        agency_name, email, password, phone, office_address, city_id, city_is_active
    )
    if details is None:
        raise RegistrationInvalid(errors)
    _register(
        actor,
        details.email,
        details.password,
        AccountType.AGENCY.value,
        lambda account: _create_agency_profile(account, details),
    )


def _record_verified(actor: Actor, account: Account) -> None:
    if account.email_verified_at is None:
        account.email_verified_at = timezone.now()
        account.save(update_fields=["email_verified_at", "updated_at"])
        audit.record(actor, "account.email_verified", "account", account.id)


def mark_email_verified(actor: Actor, account_id) -> AccountView:
    """Idempotent: a second call changes nothing and writes no second audit record."""
    _require_system(actor)
    with transaction.atomic():
        account = _locked(account_id)
        _record_verified(actor, account)
        return _view(account.id)


def issue_verification_token(actor: Actor, account_id) -> tuple[str, str] | None:
    """Issue a fresh link token and supersede the account's earlier unused ones.

    Returns `(email, token)` for the caller to mail inside the same transaction, or None unless the
    account is exactly Email-unverified (a suspended or deactivated account never gets a live link).
    The plain token is never stored.
    """
    _require_system(actor)
    key = parse_id(account_id)
    with transaction.atomic():
        account = Account.objects.select_for_update().filter(id=key).first() if key else None
        if account is None or account.state is not AccountState.EMAIL_UNVERIFIED:
            return None
        now = timezone.now()
        EmailVerificationToken.objects.filter(account=account, used_at__isnull=True).update(
            used_at=now
        )
        token = verification.new_token()
        EmailVerificationToken.objects.create(
            account=account,
            token_hash=verification.hash_token(token),
            created_at=now,
            expires_at=now + verification.TOKEN_TTL,
        )
        return account.email, token


def confirm_email(actor: Actor, token) -> None:
    """Consume a verification link and verify the account's email, in one transaction.

    Raises `link_invalid`, `link_expired` or `link_used`. The state of a Suspended or Deactivated
    account still wins over the verification. The token is never echoed or logged.
    """
    if not verification.well_formed(token):
        raise DomainError("link_invalid")
    token_hash = verification.hash_token(token)
    with transaction.atomic():
        found = EmailVerificationToken.objects.filter(token_hash=token_hash).first()
        if found is None:
            raise DomainError("link_invalid")
        # Lock order is account then token, matching `issue_verification_token`. A purge or
        # cascade between the lookup and the locks makes the link invalid, not a server error.
        account = Account.objects.select_for_update().filter(id=found.account_id).first()
        row = EmailVerificationToken.objects.select_for_update().filter(id=found.id).first()
        if account is None or row is None:
            raise DomainError("link_invalid")
        now = timezone.now()
        if row.used_at is not None:
            raise DomainError("link_used")
        if row.expires_at <= now:
            raise DomainError("link_expired")
        row.used_at = now
        row.save(update_fields=["used_at"])
        _record_verified(Actor.system(actor.request_id), account)


def request_verification_link(actor: Actor, email) -> None:
    """Send a new link, or the sign-in notice, if the address is registered; reveal nothing.

    Unknown, suspended and deactivated accounts get nothing. Each account is capped at 5 sends an
    hour with the shared `verification_email` scope, silently. Mail goes out after commit.
    """
    if not isinstance(email, str) or "\x00" in email:
        return
    email, code = check_email(email)  # the same strip and rules as registration
    if code:
        return
    with transaction.atomic():
        existing = (
            Account.objects.annotate(lowered=Lower("email")).filter(lowered=email.lower()).first()
        )
        if existing is None or existing.state not in (
            AccountState.EMAIL_UNVERIFIED,
            AccountState.ACTIVE,
        ):
            return
        key = ratelimit.derive_key("account", str(existing.id))
        if not ratelimit.hit("verification_email", key).allowed:
            return
        if existing.email_verified_at is None:
            emit_verification_requested(existing.id)
        else:
            notices.send_already_registered(existing.email)


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


@dataclass(frozen=True)
class SignedIn:
    """The result of a successful sign-in. `token` goes only into the cookie."""

    token: str
    summary: ActorSummary


def _lock_key(key: str) -> None:
    """Serialise sign-in attempts per throttle key for the rest of the transaction."""
    number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [number])


def _cap_open_sessions(account_id, now) -> None:
    """Keep at most SESSION_MAX_OPEN open sessions: the oldest beyond the cap end as replaced."""
    open_ids = list(
        Session.objects.filter(account_id=account_id, ended_at__isnull=True, expires_at__gt=now)
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)
    )
    excess = open_ids[settings.SESSION_MAX_OPEN :]
    if excess:
        Session.objects.filter(id__in=excess).update(ended_at=now, ended_reason="replaced")


def _attempt(key: str, email: str, password: str, current_token, now) -> SignedIn | None:
    """One sign-in attempt under the per-key lock. None means failed; a failure has been counted
    and committed (nothing here raises out of the transaction after counting)."""
    with transaction.atomic():
        _lock_key(key)
        if ratelimit.peek("sign_in", key, now):
            return None
        if len(password) > PASSWORD_MAX:
            ratelimit.hit("sign_in", key, now)
            return None
        account = (
            Account.objects.annotate(lowered=Lower("email")).filter(lowered=email.lower()).first()
        )
        verified = None
        if (
            account is not None
            and account.state is not AccountState.DEACTIVATED
            and is_password_usable(account.password)
        ):
            verified = account.password
        matches = check_password(password, verified or passwords.dummy_hash())
        if verified is None or not matches:
            ratelimit.hit("sign_in", key, now)
            return None
        locked = Account.objects.select_for_update().filter(id=account.id).first()
        if (
            locked is None
            or locked.state is AccountState.DEACTIVATED
            or locked.password != verified
            or locked.email.lower() != email.lower()
        ):
            ratelimit.hit("sign_in", key, now)  # changed under us: the checked password is stale
            return None
        if sessions.well_formed(current_token):
            Session.objects.filter(
                token_hash=sessions.hash_token(current_token), ended_at__isnull=True
            ).update(ended_at=now, ended_reason="replaced")
        token = sessions.new_token()
        Session.objects.create(
            account=locked,
            token_hash=sessions.hash_token(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + sessions.absolute_lifetime(),
        )
        _cap_open_sessions(locked.id, now)
        return SignedIn(token=token, summary=summarise(locked))


def sign_in(actor: Actor, email, password, network: str, current_token=None) -> SignedIn:
    """Start a session for the email and password, or raise `invalid_credentials`.

    Wrong password, unknown email, a Deactivated account and a throttled key all raise the same
    error. Attempts for one key run one at a time; the throttle is asked first, so a correct
    password during lockout also fails and no hash is computed. Only failures count. Unknown,
    Deactivated and unusable-password accounts still check the password against a dummy hash so
    timing does not separate them. Any session the caller already held (`current_token`) is ended
    as `replaced`. Email-unverified and Suspended accounts can sign in; their restrictions come
    from the capability matrix.
    """
    if not isinstance(email, str) or not isinstance(password, str):
        raise DomainError("validation_error")
    email = email.strip()
    if not email or len(email) > 254 or "\x00" in email:
        raise DomainError("invalid_credentials")
    now = timezone.now()
    key = ratelimit.derive_key("sign_in", email.lower(), network)
    result = _attempt(key, email, password, current_token, now)
    if result is None:
        raise DomainError("invalid_credentials")
    return result


def sign_out(actor: Actor, session_id) -> None:
    """End the caller's current session only, and announce it. A session already ended is
    `unauthenticated`, so a second sign-out fails."""
    require_signed_in(actor)
    key = parse_id(session_id)
    with transaction.atomic():
        ended = (
            Session.objects.filter(
                id=key, account_id=actor.account_id, ended_at__isnull=True
            ).update(ended_at=timezone.now(), ended_reason="sign_out")
            if key
            else 0
        )
        if not ended:
            raise DomainError("unauthenticated")
        emit_session_ended(actor.account_id, "sign_out")


def _end_open_sessions(
    account_id, reason: str, except_session_id=None, ended_before=None, now=None
) -> int:
    sessions_ = Session.objects.filter(account_id=account_id, ended_at__isnull=True)
    if except_session_id is not None:
        sessions_ = sessions_.exclude(id=except_session_id)
    if ended_before is not None:
        sessions_ = sessions_.filter(created_at__lte=ended_before)
    return sessions_.update(ended_at=now or timezone.now(), ended_reason=reason)


def end_account_sessions(
    actor: Actor, account_id, reason: str, except_session_id=None, ended_before=None
) -> int:
    """End every open session of the account, except `except_session_id` when given and, when
    `ended_before` is given, only those created up to then (a session opened after the change that
    caused the call is never ended by a delayed handler). System only; returns how many ended."""
    _require_system(actor)
    key = parse_id(account_id)
    keep = parse_id(except_session_id) if except_session_id is not None else None
    if (
        key is None
        or reason not in SESSION_END_REASONS - {"sign_out"}
        or (except_session_id is not None and keep is None)
        or (
            ended_before is not None
            and (not isinstance(ended_before, datetime) or ended_before.tzinfo is None)
        )
    ):
        raise DomainError("validation_error")
    return _end_open_sessions(key, reason, keep, ended_before)


def refresh_session(actor: Actor, session_id) -> None:
    """Move `last_seen_at` forward, at most once per touch interval. System only."""
    _require_system(actor)
    now = timezone.now()
    Session.objects.filter(
        id=session_id,
        ended_at__isnull=True,
        expires_at__gt=now,
        last_seen_at__lte=now - sessions.touch_interval(),
    ).update(last_seen_at=now)


# --- password reset and change (Story 1.18) -----------------------------------------------------


class PasswordInvalid(DomainError):
    """A password field failed. `fields` maps field name to a stable error code."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("validation_error")
        self.fields = dict(fields)


def _eligible_for_reset(account: Account | None) -> bool:
    """Only an existing, non-deactivated account with a usable password can be reset."""
    return (
        account is not None
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
        if account is None or row is None or account.state is AccountState.DEACTIVATED:
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
        _end_open_sessions(account.id, "password_changed", now=now)
        audit.record(
            Actor.system(actor.request_id), "account.password_reset", "account", account.id
        )
        emit_session_ended(account.id, "password_changed", ended_before=now)
        notices.send_password_reset_done(account.email)


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
        code = passwords.password_error(new, account.email)
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
        audit.record(actor, "account.password_changed", "account", account.id)
        emit_session_ended(account.id, "password_changed", session_id, ended_before=now)
        notices.send_password_changed(account.email)
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
