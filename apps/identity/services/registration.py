"""Individual and agency registration (Stories 1.15 and 1.19)."""

from collections.abc import Callable

from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.db.models.functions import Lower

from apps.identity import (
    notices,
)
from apps.identity.events import (
    emit_registered,
    emit_verification_requested,
)
from apps.identity.models import (
    Account,
    AccountType,
    AgencyProfile,
    IndividualProfile,
)
from apps.identity.selectors import (
    city_is_active,
)
from apps.identity.services.accounts import _insert_account
from apps.identity.validation import (
    AgencyDetails,
    validate_agency,
    validate_individual,
)
from apps.kernel import ratelimit
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError


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
