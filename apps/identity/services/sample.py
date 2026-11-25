"""Sample accounts for development data (Story 2.16). System actor only.

`create_sample_account` makes an Active, email-verified Individual or Agency with a filled
profile, with no mail and no event (the address is on the reserved `.invalid` domain and cannot
receive any). `remove_sample_account` deletes one, and refuses any account whose address is not
on that domain, so a real account can never be removed through it. Neither is reachable over HTTP.
"""

from django.contrib.auth.hashers import make_password
from django.db import transaction

from apps.identity import passwords, selectors
from apps.identity.models import Account, AccountType, AgencyProfile, IndividualProfile
from apps.identity.selectors import AccountView, city_is_active
from apps.identity.services._common import (
    _record_verified,
    _require_system,
    _view,
)
from apps.identity.services.accounts import _clean_email, _insert_account
from apps.identity.services.password_flows import PasswordInvalid
from apps.identity.services.profile_images import purge_profile_image
from apps.identity.validation import check_agency_name, check_full_name
from apps.kernel import audit, media, notifications
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError

# Obviously fake and never dialable: not a real Pakistani mobile, accepted by the column.
SAMPLE_PHONE = "+920000000000"
SAMPLE_OFFICE = "Sample Plaza, Sample Road (fictional)"


def create_sample_account(
    actor: Actor, email, password, account_type, name, city_id=None, description: str = ""
) -> AccountView:
    """An Active, verified sample account with its profile. `email` must be on the reserved
    domain and unused (`validation_error` otherwise); an Agency needs an active `city_id`."""
    _require_system(actor)
    email = _clean_email(email, allow_sample=True)  # the one internal bypass
    if not selectors.is_sample_email(email) or account_type not in (
        AccountType.INDIVIDUAL.value,
        AccountType.AGENCY.value,
    ):
        raise DomainError("validation_error")
    code = passwords.password_error(password, email)
    if code:
        raise PasswordInvalid({"password": code})
    if account_type == AccountType.AGENCY.value:
        agency_name, name_code = check_agency_name(name)
        if name_code or not city_is_active(city_id):
            raise DomainError("validation_error")
    else:
        full_name, name_code = check_full_name(name)
        if name_code:
            raise DomainError("validation_error")
    hashed = make_password(password)
    with transaction.atomic():
        account = _insert_account(actor, email, hashed, account_type)
        if account is None:
            raise DomainError("validation_error")
        _record_verified(actor, account)
        if account_type == AccountType.AGENCY.value:
            AgencyProfile.objects.create(
                account=account,
                agency_name=agency_name,
                business_phone=SAMPLE_PHONE,
                office_address=SAMPLE_OFFICE,
                city_id=city_id,
                description=description,
            )
        else:
            IndividualProfile.objects.create(
                account=account, full_name=full_name, phone=SAMPLE_PHONE
            )
        return _view(account.id)


def remove_sample_account(actor: Actor, account_id) -> bool:
    """Delete a sample account with its profile, sessions and tokens (the picture asset goes
    through `kernel.media`). An account that is not on the reserved domain, an administrator and
    an unknown id are left alone and give False, and so is an account that still owns stored media
    (its Listings must be removed first: `listings.services.remove_sample_listing`; the profile
    picture goes with it). The row is locked first. The audit record keeps only the former id."""
    _require_system(actor)
    key = selectors.parse_id(account_id)
    with transaction.atomic():
        account = Account.objects.select_for_update().filter(id=key).first() if key else None
        if (
            account is None
            or account.account_type == AccountType.ADMIN.value
            or not selectors.is_sample_email(account.email)
        ):
            return False
        purge_profile_image(actor, account.id)
        if media.has_assets(account.id):  # Listings (with images) remain: refuse, change nothing
            transaction.set_rollback(True)  # also undo the picture purge above
            return False
        account.delete()
        media.forget_usage(actor, key)
        notifications.delete_for_account(key)
        audit.record(actor, "account.sample_removed", "account", key)
        notifications.delete_for_account_after_commit(key)
    return True
