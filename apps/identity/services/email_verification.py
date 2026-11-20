"""Email verification: mark, issue, confirm and request a new link (Story 1.16)."""

from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone

from apps.identity import (
    notices,
    verification,
)
from apps.identity.events import (
    emit_verification_requested,
)
from apps.identity.models import (
    Account,
    EmailVerificationToken,
)
from apps.identity.selectors import (
    AccountView,
    parse_id,
)
from apps.identity.services._common import _locked, _record_verified, _require_system, _view
from apps.identity.state import AccountState
from apps.identity.validation import (
    check_email,
)
from apps.kernel import ratelimit
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError


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
