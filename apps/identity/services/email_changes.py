"""Sign-in email change: request, issue and confirm (Story 1.20)."""

import hashlib
from dataclasses import dataclass, field

from django.contrib.auth.hashers import check_password, is_password_usable
from django.db import IntegrityError, transaction
from django.db.models import Value
from django.db.models.functions import Lower
from django.utils import timezone

from apps.identity import (
    email_change,
    notices,
    passwords,
    verification,
)
from apps.identity.events import (
    emit_email_change_requested,
)
from apps.identity.models import (
    Account,
    AccountType,
    EmailChangeToken,
    EmailVerificationToken,
    PasswordResetToken,
)
from apps.identity.passwords import PASSWORD_MAX
from apps.identity.selectors import (
    parse_id,
)
from apps.identity.services._common import (
    EMAIL_CONSTRAINT,
    _email_taken,
    _lock_key,
    _record_verified,
    _require_owner_actor,
    _require_system,
)
from apps.identity.services.profile import FieldsInvalid
from apps.identity.state import AccountState
from apps.identity.validation import (
    check_email,
)
from apps.kernel import audit, notifications, ratelimit
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError


def _email_change_attempt(key: str, actor: Actor, typed: str, current: str, now) -> bool:
    """One request under the shared failed-password lock (the password change's own key, so the
    two routes cannot probe the password separately). False means the current password was wrong
    or the throttle is full; that has been counted and committed. Other failures raise.

    The work is the same whatever the new address is: no lookup of it here. A row is created for
    every accepted request; the after-commit handler decides what, if anything, is sent.
    """
    with transaction.atomic():
        _lock_key(key)
        if ratelimit.peek("password_change_failed", key, now):
            return False
        account = Account.objects.select_for_update().filter(id=actor.account_id).first()
        if account is None or account.state is AccountState.DEACTIVATED:
            raise DomainError("unauthenticated")
        if account.account_type == AccountType.ADMIN:
            raise DomainError("forbidden")
        usable = is_password_usable(account.password)
        matches = (
            len(current) <= PASSWORD_MAX
            and check_password(current, account.password if usable else passwords.dummy_hash())
            and usable
        )
        if not matches:
            ratelimit.hit("password_change_failed", key, now)
            return False
        # Only a verified password spends the per-account request budget (a wrong or capped one
        # is limited by the failure throttle instead). Over budget raises 429 and rolls back.
        ratelimit.enforce("email_change_request", ratelimit.derive_key("account", str(account.id)))
        now = timezone.now()
        row = EmailChangeToken.objects.create(
            account=account,
            new_email=typed,
            token_hash=verification.hash_token(verification.new_token()),  # throwaway: no link yet
            created_at=now,
            expires_at=now + email_change.TOKEN_TTL,
        )
        emit_email_change_requested(account.id, row.id)
        return True


def request_email_change(actor: Actor, new_email, current_password) -> None:
    """Ask to change the sign-in email. Returns nothing; the caller answers the same 202 always.

    Needs the current password, checked under the password change's failed-attempt throttle (5 per
    15 minutes, silent `incorrect`, never 429, one attempt at a time per account). Only a verified
    password spends the `email_change_request` budget (429 once exhausted). Emits
    `account.email_change_requested` (ids only). The new address is not looked up here; the
    handler sends the link only to a free one.
    """
    _require_owner_actor(actor)
    if not isinstance(new_email, str) or not isinstance(current_password, str):
        raise DomainError("validation_error")
    typed, code = check_email(new_email)
    if code:
        raise FieldsInvalid({"new_email": code})
    key = ratelimit.derive_key("password_change_failed", str(actor.account_id))
    if not _email_change_attempt(key, actor, typed, current_password, timezone.now()):
        raise FieldsInvalid({"current_password": "incorrect"})


@dataclass(frozen=True)
class EmailChangeDelivery:
    """What the handler must send. The current address always gets the heads-up notice, whatever
    the branch, so the owner's inbox cannot tell the branches apart. `kind` adds `confirm` (the
    link to `target`, the free new address) or `taken` (the "already registered" notice to
    `target`, the other account's address) or `none`."""

    kind: str
    current_email: str = field(repr=False)
    target: str = field(default="", repr=False)
    token: str = field(default="", repr=False)


def _target_cap_allows(address: str) -> bool:
    """Silent per-target-address cap on mail to an arbitrary address (mail-bomb guard)."""
    digest = hashlib.sha256(address.lower().encode()).hexdigest()
    return ratelimit.hit("verification_email", ratelimit.derive_key("address", digest)).allowed


def issue_email_change(actor: Actor, account_id, change_request_id) -> EmailChangeDelivery | None:
    """Decide and prepare the mail for a change request, after commit, in the caller's transaction.

    None only when the account cannot change (unknown, Deactivated, administrator). Otherwise the
    delivery always has the heads-up for the current address, plus:
    - `confirm`: the address is free; the row's throwaway hash is replaced by a real token, the
      row is marked `link_issued` and only then are the account's earlier issued links superseded;
    - `taken`: another Email-unverified or Active account has the address (never a link);
    - `none`: same address as the current one, a Suspended or Deactivated owner, a used, expired
      or already issued row, or a silent cap (per requesting account, per target address).
    A request that issues no link never touches the owner's valid pending link.
    """
    _require_system(actor)
    account_key, change_key = parse_id(account_id), parse_id(change_request_id)
    if account_key is None or change_key is None:
        return None
    with transaction.atomic():
        account = Account.objects.select_for_update().filter(id=account_key).first()
        if (
            account is None
            or account.state is AccountState.DEACTIVATED
            or account.account_type == AccountType.ADMIN
        ):
            return None
        none = EmailChangeDelivery("none", account.email)
        row = (
            EmailChangeToken.objects.select_for_update()
            .filter(id=change_key, account_id=account.id)
            .first()
        )
        now = timezone.now()
        if row is None or row.used_at is not None or row.link_issued or row.expires_at <= now:
            return none
        owner = (
            Account.objects.annotate(lowered=Lower("email"))
            .filter(lowered=Lower(Value(row.new_email)))
            .first()
        )
        if owner is not None and (
            owner.id == account.id
            or owner.state not in (AccountState.EMAIL_UNVERIFIED, AccountState.ACTIVE)
        ):
            return none  # the same address, or a Suspended or Deactivated owner: nobody is mailed
        target = owner.email if owner is not None else row.new_email
        if not ratelimit.hit(
            "verification_email", ratelimit.derive_key("account", str(account.id))
        ).allowed or not _target_cap_allows(target):
            return none
        if owner is not None:
            return EmailChangeDelivery("taken", account.email, target)
        token = verification.new_token()
        row.token_hash = verification.hash_token(token)
        row.expires_at = now + email_change.TOKEN_TTL
        row.link_issued = True
        row.save(update_fields=["token_hash", "expires_at", "link_issued"])
        EmailChangeToken.objects.filter(
            account=account, used_at__isnull=True, link_issued=True
        ).exclude(id=row.id).update(used_at=now)
        return EmailChangeDelivery("confirm", account.email, target, token)


def confirm_email_change(actor: Actor, token) -> None:
    """Consume a change link and swap the sign-in address, in one transaction.

    Raises `link_invalid`, `link_used` or `link_expired` (in that order of checks after the
    lookup); a Deactivated account, or an address another account has taken meanwhile, is
    `link_invalid` with no hint of why. The link proves ownership of the new address, so an
    unverified email becomes verified. Sessions are not ended (the request needed the password).
    Outstanding verification and reset links, which were mailed to the old address, are voided.
    """
    if not verification.well_formed(token):
        raise DomainError("link_invalid")
    token_hash = verification.hash_token(token)
    system = Actor.system(actor.request_id)
    with transaction.atomic():
        found = EmailChangeToken.objects.filter(token_hash=token_hash, link_issued=True).first()
        if found is None:
            raise DomainError("link_invalid")
        # Lock order is account then token, as in the other confirmations and `issue_email_change`.
        account = Account.objects.select_for_update().filter(id=found.account_id).first()
        row = EmailChangeToken.objects.select_for_update().filter(id=found.id).first()
        if account is None or row is None or account.account_type == AccountType.ADMIN:
            raise DomainError("link_invalid")  # an administrator's address never changes by link
        now = timezone.now()
        if row.used_at is not None:
            raise DomainError("link_used")
        if row.expires_at <= now:
            raise DomainError("link_expired")
        if account.state is AccountState.DEACTIVATED or _email_taken(row.new_email):
            raise DomainError("link_invalid")
        old_email = account.email
        account.email = row.new_email
        try:
            with transaction.atomic():
                account.save(update_fields=["email", "updated_at"])
        except IntegrityError as exc:
            diag = getattr(exc.__cause__, "diag", None)
            if getattr(diag, "constraint_name", None) != EMAIL_CONSTRAINT:
                raise
            raise DomainError("link_invalid") from None
        row.used_at = now
        row.save(update_fields=["used_at"])
        EmailChangeToken.objects.filter(account=account, used_at__isnull=True).update(used_at=now)
        EmailVerificationToken.objects.filter(account=account, used_at__isnull=True).update(
            used_at=now
        )
        PasswordResetToken.objects.filter(account=account, used_at__isnull=True).update(used_at=now)
        _record_verified(system, account)
        audit.record(system, "account.email_changed", "account", account.id)
        notices.send_email_changed(old_email)
        notifications.notify(account.id, "account.email_changed", {})  # last: see docs
