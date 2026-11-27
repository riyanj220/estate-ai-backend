"""The optional TOTP second factor of administrators (Story 10.10, FR-10).

Enrolment: the password again, then a first code from the new secret. Once confirmed, the
administrator door needs a code as well as the password. A code works once. Nothing here removes
an enrolment: that is `provision_admin reset-second-factor` only.
"""

from dataclasses import dataclass, field

from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.utils import timezone

from apps.identity import totp
from apps.identity.models import Account, AccountType, SecondFactor
from apps.identity.passwords import PASSWORD_MAX
from apps.identity.selectors import parse_id
from apps.identity.services._common import _end_open_sessions, _lock_key
from apps.identity.services.profile import FieldsInvalid
from apps.identity.state import AccountState
from apps.kernel import audit, ratelimit
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError

LIMIT_SCOPE = "admin_second_factor"  # wrong passwords and codes while enrolling


@dataclass(frozen=True)
class Enrolment:
    """Shown once, to the enrolling administrator."""

    secret: str = field(repr=False)
    otpauth_uri: str = field(repr=False)


def _admin_account(actor: Actor) -> Account:
    """Inside a transaction: the signed-in administrator's account, locked."""
    account = Account.objects.select_for_update().filter(id=actor.account_id).first()
    if (
        account is None
        or account.account_type != AccountType.ADMIN
        or account.state is AccountState.DEACTIVATED
    ):
        raise DomainError("forbidden")
    return account


def second_factor_enrolled(actor: Actor) -> bool:
    require_admin(actor)
    return SecondFactor.objects.filter(
        account_id=actor.account_id, confirmed_at__isnull=False
    ).exists()


def _throttle_key(actor: Actor) -> str:
    key = ratelimit.derive_key(LIMIT_SCOPE, str(actor.account_id))
    _lock_key(key)  # one attempt at a time
    if ratelimit.peek(LIMIT_SCOPE, key):
        raise ratelimit.RateLimited(ratelimit.limit_for(LIMIT_SCOPE).window_seconds)
    return key


def start_second_factor(actor: Actor, password) -> Enrolment:
    """A new pending secret (replacing an unconfirmed one). The password is asked again so that
    a borrowed session cannot enrol someone else's authenticator. `conflict` once enrolled."""
    require_admin(actor)
    if not isinstance(password, str) or not password:
        raise FieldsInvalid({"password": "required"})
    with transaction.atomic():
        key = _throttle_key(actor)
        account = _admin_account(actor)
        if SecondFactor.objects.filter(account=account, confirmed_at__isnull=False).exists():
            raise DomainError("conflict")
        correct = len(password) <= PASSWORD_MAX and check_password(password, account.password)
        if correct:
            secret = totp.new_secret()
            SecondFactor.objects.update_or_create(
                account=account,
                defaults={"secret_encrypted": totp.encrypt(secret), "last_used_step": None},
            )
        else:
            ratelimit.hit(LIMIT_SCOPE, key)  # counted, so raise only after the commit
    if not correct:
        raise FieldsInvalid({"password": "incorrect"})
    return Enrolment(secret, totp.otpauth_uri(secret, account.email))


def confirm_second_factor(actor: Actor, session_id, code) -> None:
    """Turn the pending secret on with a first code, and end the other sessions (they were opened
    without it). `conflict` when nothing is pending."""
    require_admin(actor)
    keep = parse_id(session_id)
    with transaction.atomic():
        key = _throttle_key(actor)
        account = _admin_account(actor)
        row = SecondFactor.objects.filter(account=account, confirmed_at__isnull=True).first()
        if row is None:
            raise DomainError("conflict")
        secret = totp.decrypt(row.secret_encrypted)
        now = timezone.now()
        step = totp.matching_step(secret, code, now, None) if secret else None
        if step is None:
            ratelimit.hit(LIMIT_SCOPE, key)
            audit.record(actor, "admin.second_factor_failed", "account", account.id)
        else:
            row.confirmed_at = now
            row.last_used_step = step
            row.save(update_fields=["confirmed_at", "last_used_step", "updated_at"])
            _end_open_sessions(account.id, "replaced", keep, now=now)
            audit.record(actor, "admin.second_factor_enrolled", "account", account.id)
    if step is None:
        raise FieldsInvalid({"code": "invalid"})


def _use_code_at_sign_in(account_id, code, now) -> bool | None:
    """Inside the sign-in transaction, after the password matched. None when the account is not
    enrolled; True when the code is good (and now spent); False otherwise."""
    row = (
        SecondFactor.objects.select_for_update()
        .filter(account_id=account_id, confirmed_at__isnull=False)
        .first()
    )
    if row is None:
        return None
    secret = totp.decrypt(row.secret_encrypted)
    step = totp.matching_step(secret, code, now, row.last_used_step) if secret else None
    if step is None:
        return False
    row.last_used_step = step
    row.save(update_fields=["last_used_step", "updated_at"])
    return True
