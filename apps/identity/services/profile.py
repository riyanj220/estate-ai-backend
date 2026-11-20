"""Individual and agency profile read and update (Stories 1.20 and 1.21)."""

from collections.abc import Mapping

from django.db import transaction

from apps.identity import (
    selectors,
)
from apps.identity.models import (
    Account,
    AccountType,
    AgencyProfile,
    IndividualProfile,
)
from apps.identity.selectors import (
    AgencyProfileView,
    ProfileView,
    agency_profile_view,
    city_is_active,
    profile_view,
)
from apps.identity.services._common import _require_owner_actor
from apps.identity.state import AccountState
from apps.identity.validation import (
    check_agency_name,
    check_city_id,
    check_description,
    check_full_name,
    check_office_address,
    check_phone,
)
from apps.kernel import audit
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError


class FieldsInvalid(DomainError):
    """Profile or email-change fields failed. `fields` maps field name to a stable error code."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("validation_error")
        self.fields = dict(fields)


def get_profile(actor: Actor) -> ProfileView:
    """The signed-in Individual's own profile. Any other account type is `forbidden`."""
    _require_owner_actor(actor)
    return selectors.get_profile(actor.account_id)


_PROFILE_CHECKS = {"full_name": check_full_name, "phone": check_phone}


def update_profile(actor: Actor, changes: Mapping) -> ProfileView:
    """Change the name and/or phone of the signed-in Individual; nothing else is updatable.

    Every failing field is reported at once (`FieldsInvalid`); the phone stays mandatory. An
    unchanged submission writes nothing, not even an audit record. Serialised by the account lock.
    """
    _require_owner_actor(actor)
    if not isinstance(changes, Mapping) or not changes or set(changes) - set(_PROFILE_CHECKS):
        raise DomainError("validation_error")
    with transaction.atomic():
        account = Account.objects.select_for_update().filter(id=actor.account_id).first()
        if account is None or account.state is AccountState.DEACTIVATED:
            raise DomainError("unauthenticated")
        profile = (
            IndividualProfile.objects.select_for_update().filter(account_id=account.id).first()
            if account.account_type == AccountType.INDIVIDUAL
            else None
        )
        if profile is None:
            raise DomainError("forbidden")
        cleaned, errors = {}, {}
        for name, check in _PROFILE_CHECKS.items():
            if name in changes:
                value, code = check(changes[name])
                if code:
                    errors[name] = code
                else:
                    cleaned[name] = value
        if errors:
            raise FieldsInvalid(errors)
        changed = {k: v for k, v in cleaned.items() if getattr(profile, k) != v}
        if changed:
            for name, value in changed.items():
                setattr(profile, name, value)
            profile.save(update_fields=[*changed, "updated_at"])
            audit.record(actor, "account.profile_updated", "account", account.id)
        return profile_view(account, profile)


def get_agency_profile(actor: Actor) -> AgencyProfileView:
    """The signed-in Agency's own profile. Any other account type is `forbidden`."""
    _require_owner_actor(actor)
    return selectors.get_agency_profile(actor.account_id)


def require_agency(actor: Actor) -> None:
    """Refuse anything but a live Agency account (`forbidden`, `unauthenticated`), without
    building the profile view. The API calls it before judging a request body."""
    _require_owner_actor(actor)
    account = Account.objects.filter(id=actor.account_id).first()
    if account is None or account.state is AccountState.DEACTIVATED:
        raise DomainError("unauthenticated")
    if account.account_type != AccountType.AGENCY:
        raise DomainError("forbidden")


_AGENCY_PROFILE_CHECKS = {
    "agency_name": check_agency_name,
    "description": check_description,
    "city_id": check_city_id,
    "business_phone": check_phone,
    "office_address": check_office_address,
}


def update_agency_profile(actor: Actor, changes: Mapping) -> AgencyProfileView:
    """Change the business details of the signed-in Agency; nothing else is updatable.

    Name, phone, address and city stay mandatory; only the description may be cleared. The
    account type is refused before the body is judged. Every failing field is reported at once
    (`FieldsInvalid`); a city that differs from the stored one is looked up only once the other
    fields pass, inside the transaction (this narrows, but with no lock on the City row does not
    remove, a race with a concurrent deactivation). Resending the stored city is never looked up,
    so it cannot block other edits. An unchanged submission writes nothing, not even an audit
    record. Serialised by the account lock (account, then profile).
    """
    _require_owner_actor(actor)
    with transaction.atomic():
        account = Account.objects.select_for_update().filter(id=actor.account_id).first()
        if account is None or account.state is AccountState.DEACTIVATED:
            raise DomainError("unauthenticated")
        profile = (
            AgencyProfile.objects.select_for_update().filter(account_id=account.id).first()
            if account.account_type == AccountType.AGENCY
            else None
        )
        if profile is None:
            raise DomainError("forbidden")
        if (
            not isinstance(changes, Mapping)
            or not changes
            or set(changes) - set(_AGENCY_PROFILE_CHECKS)
        ):
            raise DomainError("validation_error")
        cleaned, errors = {}, {}
        for name, check in _AGENCY_PROFILE_CHECKS.items():
            if name in changes:
                value, code = check(changes[name])
                if code:
                    errors[name] = code
                else:
                    cleaned[name] = value
        if (
            not errors
            and "city_id" in cleaned
            and cleaned["city_id"] != profile.city_id
            and not city_is_active(cleaned["city_id"])
        ):
            errors["city_id"] = "invalid_city"
        if errors:
            raise FieldsInvalid(errors)
        changed = {k: v for k, v in cleaned.items() if getattr(profile, k) != v}
        if changed:
            for name, value in changed.items():
                setattr(profile, name, value)
            profile.save(update_fields=[*changed, "updated_at"])
            audit.record(actor, "account.profile_updated", "account", account.id)
        return agency_profile_view(account, profile)
