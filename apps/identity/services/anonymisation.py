"""Anonymising a Deactivated account (Story 10.2, FR-8). The row stays, so quota, moderation and
audit history keep pointing at it; nothing personal remains on it.
"""

from django.contrib.auth.hashers import make_password
from django.db import transaction

from apps.identity.models import (
    Account,
    AgencyProfile,
    EmailChangeToken,
    EmailVerificationToken,
    IndividualProfile,
    PasswordResetToken,
)
from apps.identity.state import AccountState
from apps.kernel import audit, notifications
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError

# `.invalid` can never receive mail. The original address is free to register again, as a new
# account with its own id and so with no quota history.
CLOSED_EMAIL_DOMAIN = "closed.estate-ai.invalid"
DELETED_NAME = "Deleted user"
DELETED_AGENCY_NAME = "Deleted agency"


def anonymise_account(actor: Actor, account_id) -> None:
    """Replace the email and profile fields, make the password unusable and remove the
    account's tokens and notifications. Only a Deactivated account (`conflict` otherwise),
    inside the caller's transaction."""
    require_admin(actor)
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("anonymise_account must run inside the closure's transaction")
    account = Account.objects.select_for_update().filter(id=account_id).first()
    if account is None:
        raise DomainError("not_found")
    if account.state is not AccountState.DEACTIVATED:
        raise DomainError("conflict")
    account.email = f"closed-{account.id.hex}@{CLOSED_EMAIL_DOMAIN}"
    account.password = make_password(None)
    account.save(update_fields=["email", "password", "updated_at"])
    IndividualProfile.objects.filter(account=account).update(full_name=DELETED_NAME, phone="")
    AgencyProfile.objects.filter(account=account).update(
        agency_name=DELETED_AGENCY_NAME, business_phone="", office_address="", description=""
    )
    for model in (EmailVerificationToken, PasswordResetToken, EmailChangeToken):
        model.objects.filter(account=account).delete()  # they carry addresses or open a door
    audit.record(actor, "identity.account_erased", "account", account.id)
    notifications.delete_for_account(account.id)
    notifications.delete_for_account_after_commit(account.id)  # any queued in this transaction
