"""Account creation and the administrator state changes: suspend, reactivate, deactivate."""

from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.identity.events import (
    emit_session_ended,
)
from apps.identity.models import (
    Account,
    AccountType,
)
from apps.identity.passwords import PASSWORD_MAX
from apps.identity.selectors import (
    AccountView,
)
from apps.identity.services._common import (
    EMAIL_CONSTRAINT,
    _email_taken,
    _locked,
    _require_system,
    _view,
    _void_email_change_links,
)
from apps.identity.services.profile_images import purge_profile_image
from apps.identity.validation import (
    check_email,
)
from apps.kernel import audit
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError
from apps.kernel.privacy.patterns import LONG_DIGIT_RUN, reject_contact_details


def _clean_email(email, *, allow_sample: bool = False) -> str:
    email, code = check_email(email, allow_sample=allow_sample)
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


def _require_manageable(actor: Actor, account: Account) -> None:
    """Administrators never suspend, reactivate or deactivate themselves or other administrators."""
    if account.account_type == AccountType.ADMIN or account.id == actor.account_id:
        raise DomainError("validation_error")


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
        _void_email_change_links(account, account.deactivated_at)
        purge_profile_image(actor, account.id)  # AD-23: the picture or logo goes with the account
        audit.record(actor, "account.deactivated", "account", account.id)
        emit_session_ended(account.id, "deactivated")
        return _view(account.id)
