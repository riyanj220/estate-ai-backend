"""Account creation and the administrator deactivation."""

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
    _end_open_sessions,
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


def _clean_email(email, *, allow_sample: bool = False) -> str:
    email, code = check_email(email, allow_sample=allow_sample)
    if code:
        raise DomainError("validation_error")
    return email


def _require_manageable(actor: Actor, account: Account) -> None:
    """Administrators never deactivate themselves or other administrators."""
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


def deactivate(actor: Actor, account_id) -> AccountView:
    """Deactivate an account for good and end its sessions. The event, after commit, closes
    live connections."""
    require_admin(actor)
    with transaction.atomic():
        account = _locked(account_id)
        _require_manageable(actor, account)
        account.deactivated_at = timezone.now()
        account.save(update_fields=["deactivated_at", "updated_at"])
        _void_email_change_links(account, account.deactivated_at)
        _end_open_sessions(account.id, "deactivated", now=account.deactivated_at)
        purge_profile_image(actor, account.id)  # AD-23: the picture or logo goes with the account
        audit.record(actor, "account.deactivated", "account", account.id)
        emit_session_ended(account.id, "deactivated")
        return _view(account.id)
