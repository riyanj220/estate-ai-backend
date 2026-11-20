"""Sign-in (ordinary and administrator), sign-out and session upkeep (Story 1.17)."""

import logging
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.contrib.auth.hashers import check_password, is_password_usable
from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone

from apps.identity import (
    passwords,
    sessions,
)
from apps.identity.events import (
    SESSION_END_REASONS,
    emit_session_ended,
)
from apps.identity.models import (
    Account,
    AccountType,
    Session,
)
from apps.identity.passwords import PASSWORD_MAX
from apps.identity.selectors import (
    ActorSummary,
    parse_id,
    summarise,
)
from apps.identity.services._common import _end_open_sessions, _lock_key, _require_system
from apps.identity.state import AccountState
from apps.kernel import audit, ratelimit
from apps.kernel.actor import Actor, require_signed_in
from apps.kernel.errors import DomainError

logger = logging.getLogger("estate.identity")


@dataclass(frozen=True)
class SignedIn:
    """The result of a successful sign-in. `token` goes only into the cookie."""

    token: str
    summary: ActorSummary


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


ADMIN_ACCOUNT_SCOPE = "admin_sign_in_account"


def _count_failure(scope: str, key: str, account_key: str | None, now) -> bool:
    """Count one failed attempt; for the administrator door also against the per-account ceiling.
    True while the per-account ceiling has not been passed (an audit record is still wanted)."""
    ratelimit.hit(scope, key, now)
    if account_key is None:
        return True
    return ratelimit.hit(ADMIN_ACCOUNT_SCOPE, account_key, now).allowed


def _audit_failed_admin_sign_in(account_id, request_id: str) -> None:
    """Written after the failure count has committed, so an audit fault can neither undo the
    count nor turn the generic 401 into a 500. Logged by exception class only."""
    try:
        with transaction.atomic():
            audit.record(Actor.visitor(request_id), "admin.sign_in_failed", "account", account_id)
    except Exception as exc:
        logger.warning(
            "admin_sign_in_audit_failure",
            extra={"event": "admin_sign_in_audit_failure", "exception_class": type(exc).__name__},
        )


# The two doors share this one function on purpose: the only differences are the account-type
# test, the extra per-account ceiling and the audit records, all keyed off `admin`.
def _attempt(
    scope: str, key: str, email: str, password: str, current_token, now, admin: bool, request_id=""
) -> SignedIn | None:
    """One sign-in attempt under the per-key lock. None means failed; a failure has been counted
    and committed (nothing here raises out of the transaction after counting).

    `admin` selects the door: the ordinary one refuses administrator accounts and the
    administrator one refuses everyone else, both as an ordinary wrong password (the dummy hash
    is checked instead, so timing does not tell them apart). The administrator door also keeps a
    per-account failure ceiling (by email only, any network) that answers silently like the
    per-(email, network) cap. A throttled attempt still costs one dummy-hash comparison.
    """
    account_key = ratelimit.derive_key(ADMIN_ACCOUNT_SCOPE, email.lower()) if admin else None
    failed_admin_id = None
    with transaction.atomic():
        if account_key is not None:  # fixed lock order: account, then (email, network)
            _lock_key(account_key)
        _lock_key(key)
        if ratelimit.peek(scope, key, now) or (
            account_key is not None and ratelimit.peek(ADMIN_ACCOUNT_SCOPE, account_key, now)
        ):
            check_password("throttled", passwords.dummy_hash())  # same cost as a real attempt
            return None
        if len(password) > PASSWORD_MAX:
            _count_failure(scope, key, account_key, now)
            return None
        account = (
            Account.objects.annotate(lowered=Lower("email")).filter(lowered=email.lower()).first()
        )
        verified = None
        if (
            account is not None
            and (account.account_type == AccountType.ADMIN) is admin
            and account.state is not AccountState.DEACTIVATED
            and is_password_usable(account.password)
        ):
            verified = account.password
        matches = check_password(password, verified or passwords.dummy_hash())
        if verified is None or not matches:
            wanted = _count_failure(scope, key, account_key, now)
            if admin and verified is not None and wanted:  # an existing active administrator
                failed_admin_id = account.id
        else:
            locked = Account.objects.select_for_update().filter(id=account.id).first()
            if (
                locked is None
                or locked.state is AccountState.DEACTIVATED
                or locked.password != verified
                or locked.email.lower() != email.lower()
            ):
                _count_failure(scope, key, account_key, now)  # changed under us: stale password
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
                expires_at=now + sessions.absolute_lifetime_for(locked.account_type),
            )
            _cap_open_sessions(locked.id, now)
            if admin:
                audit.record(
                    Actor.admin(locked.id, request_id), "admin.signed_in", "account", locked.id
                )
            return SignedIn(token=token, summary=summarise(locked))
    if failed_admin_id is not None:
        _audit_failed_admin_sign_in(failed_admin_id, request_id)
    return None


def _sign_in(
    scope: str, actor: Actor, email, password, network: str, current_token, admin: bool
) -> SignedIn:
    if not isinstance(email, str) or not isinstance(password, str):
        raise DomainError("validation_error")
    email = email.strip()
    if not email or len(email) > 254 or "\x00" in email:
        raise DomainError("invalid_credentials")
    now = timezone.now()
    key = ratelimit.derive_key(scope, email.lower(), network)
    result = _attempt(scope, key, email, password, current_token, now, admin, actor.request_id)
    if result is None:
        raise DomainError("invalid_credentials")
    return result


def sign_in(actor: Actor, email, password, network: str, current_token=None) -> SignedIn:
    """Start a session for the email and password, or raise `invalid_credentials`.

    Wrong password, unknown email, a Deactivated account and a throttled key all raise the same
    error. Attempts for one key run one at a time; the throttle is asked first, so a correct
    password during lockout also fails and no hash is computed. Only failures count. Unknown,
    Deactivated and unusable-password accounts still check the password against a dummy hash so
    timing does not separate them. Any session the caller already held (`current_token`) is ended
    as `replaced`. Email-unverified and Suspended accounts can sign in; their restrictions come
    from the capability matrix. Administrator accounts are refused here like any wrong password:
    they sign in through `admin_sign_in` only.
    """
    return _sign_in("sign_in", actor, email, password, network, current_token, admin=False)


def admin_sign_in(actor: Actor, email, password, network: str, current_token=None) -> SignedIn:
    """Start an administrator session (Story 1.22), or raise the same `invalid_credentials`.

    The mirror of `sign_in`: only an administrator account that is not Deactivated can succeed;
    every other outcome (stranger, wrong password, disabled, throttled) is identical. Failures
    are throttled under `admin_sign_in` and counted only; the cap answers silently. A success is
    audited as `admin.signed_in`, a failure against an existing active administrator as
    `admin.sign_in_failed`; unknown emails and non-administrators write nothing.
    """
    return _sign_in("admin_sign_in", actor, email, password, network, current_token, admin=True)


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
