"""Administrator provisioning: create, disable, enable, remove, reset (Story 1.22)."""

from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from apps.identity import (
    passwords,
)
from apps.identity.models import (
    Account,
    AccountType,
    EmailVerificationToken,
    PasswordResetToken,
)
from apps.identity.selectors import (
    AccountView,
)
from apps.identity.services._common import (
    EMAIL_CONSTRAINT,
    _email_taken,
    _end_open_sessions,
    _require_system,
    _view,
    _void_email_change_links,
)
from apps.identity.services.password_flows import PasswordInvalid
from apps.identity.validation import (
    check_email,
)
from apps.kernel import audit, notifications
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError


class AdminRefused(DomainError):
    """A provisioning request was refused. `reason` is a stable key the command turns into a
    one-line message; it never carries the email or the password."""

    def __init__(self, reason: str) -> None:
        super().__init__("validation_error")
        self.reason = reason


def _find_admin(email) -> Account:
    """The administrator account for an address, locked. Anything else is refused: an unknown
    address and an Individual or Agency account give the same `not_an_administrator`."""
    if not isinstance(email, str) or "\x00" in email:
        raise AdminRefused("invalid_email")
    cleaned, code = check_email(email)
    if code:
        raise AdminRefused("invalid_email")
    account = (
        Account.objects.select_for_update()
        .annotate(lowered=Lower("email"))
        .filter(lowered=cleaned.lower())
        .first()
    )
    if account is None or account.account_type != AccountType.ADMIN:
        raise AdminRefused("not_an_administrator")
    return account


def _void_account_tokens(account: Account, now) -> None:
    PasswordResetToken.objects.filter(account=account, used_at__isnull=True).update(used_at=now)
    EmailVerificationToken.objects.filter(account=account, used_at__isnull=True).update(used_at=now)
    _void_email_change_links(account, now)


def provision_admin(actor: Actor, email, password) -> AccountView:
    """Create an administrator: type `admin`, email verified, Active, no profile.

    System only (the platform command). The address needs the usual email rules and no account of
    any kind may hold it; the password must pass the strong administrator policy
    (`PasswordInvalid` otherwise). Writes one `admin.provisioned` record and nothing on refusal.
    """
    _require_system(actor)
    cleaned, code = check_email(email) if isinstance(email, str) else ("", "invalid")
    if code or "\x00" in cleaned:
        raise AdminRefused("invalid_email")
    code = passwords.admin_password_error(password, cleaned)
    if code:
        raise PasswordInvalid({"password": code})
    hashed = make_password(password)
    with transaction.atomic():
        if _email_taken(cleaned):
            raise AdminRefused("email_taken")
        try:
            with transaction.atomic():
                account = Account.objects.create(
                    email=cleaned,
                    password=hashed,
                    account_type=AccountType.ADMIN.value,
                    email_verified_at=timezone.now(),
                )
        except IntegrityError as exc:
            diag = getattr(exc.__cause__, "diag", None)
            if getattr(diag, "constraint_name", None) != EMAIL_CONSTRAINT:
                raise
            raise AdminRefused("email_taken") from None
        audit.record(actor, "admin.provisioned", "account", account.id)
        return _view(account.id)


def _lock_active_admins() -> list:
    """Lock every active administrator in id order and return their ids. Every action that
    changes which administrators are active takes this lock first (then the target row), so
    concurrent disable, enable and remove calls cannot deadlock or leave nobody active."""
    return list(
        Account.objects.select_for_update()
        .filter(account_type=AccountType.ADMIN, deactivated_at__isnull=True)
        .order_by("id")
        .values_list("id", flat=True)
    )


def disable_admin(actor: Actor, email, force_last: bool = False) -> bool:
    """Deactivate an administrator so it cannot sign in, and end every open session now.

    True when something changed; False (nothing else written, no second audit record) when the
    administrator was already disabled. The last active administrator is kept unless
    `force_last`; `admin.disabled` records whether the flag was needed (`forced`). Non-
    administrators and unknown addresses are refused.
    """
    _require_system(actor)
    with transaction.atomic():
        active = _lock_active_admins()
        account = _find_admin(email)
        now = timezone.now()
        _end_open_sessions(account.id, "deactivated", now=now)
        if account.deactivated_at is not None:
            return False
        last = active == [account.id]
        if last and not force_last:
            raise AdminRefused("last_administrator")
        account.deactivated_at = now
        account.save(update_fields=["deactivated_at", "updated_at"])
        _void_account_tokens(account, now)
        audit.record(
            actor, "admin.disabled", "account", account.id, {"forced": "yes" if last else "no"}
        )
        return True


def enable_admin(actor: Actor, email) -> bool:
    """Clear the Deactivated state of a disabled administrator (its sessions stay ended).

    True when something changed; False when it was already enabled. Non-administrators and
    unknown addresses are refused. Audit `admin.enabled`.
    """
    _require_system(actor)
    with transaction.atomic():
        _lock_active_admins()
        account = _find_admin(email)
        if account.deactivated_at is None:
            return False
        account.deactivated_at = None
        account.save(update_fields=["deactivated_at", "updated_at"])
        audit.record(actor, "admin.enabled", "account", account.id)
        return True


def remove_admin(actor: Actor, email, force_last: bool = False) -> None:
    """Delete an administrator account (sessions and tokens cascade; notifications are deleted).

    The last active administrator is kept unless `force_last`; `admin.removed` records whether
    the flag was needed (`forced`). The target is the former account id (the audit table keeps
    no foreign key to accounts).
    """
    _require_system(actor)
    with transaction.atomic():
        active = _lock_active_admins()
        account = _find_admin(email)
        last = active == [account.id]
        if last and not force_last:
            raise AdminRefused("last_administrator")
        account_id = account.id
        account.delete()
        notifications.delete_for_account(account_id)
        audit.record(
            actor, "admin.removed", "account", account_id, {"forced": "yes" if last else "no"}
        )
        # Last, so a notification queued earlier in this transaction (stored by its own
        # after-commit callback) is removed too.
        notifications.delete_for_account_after_commit(account_id)


def reset_admin_password(actor: Actor, email, password) -> None:
    """Set a new administrator password, end every open session and void outstanding tokens.

    The only recovery for a forgotten administrator password. The strong policy applies
    (`PasswordInvalid`); a non-administrator is refused before the password is judged.
    """
    _require_system(actor)
    with transaction.atomic():
        account = _find_admin(email)
        code = passwords.admin_password_error(password, account.email)
        if code:
            raise PasswordInvalid({"password": code})
        now = timezone.now()
        account.password = make_password(password)
        account.save(update_fields=["password", "updated_at"])
        _end_open_sessions(account.id, "password_changed", now=now)
        _void_account_tokens(account, now)
        audit.record(actor, "admin.password_reset", "account", account.id)
