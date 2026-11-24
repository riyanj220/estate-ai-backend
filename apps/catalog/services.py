"""Write path for catalog data. Only this module (and data migrations) writes catalog tables."""

import unicodedata

from django.db import IntegrityError, transaction
from django.db.models.functions import Lower

from apps.catalog.models import LOCALITY_KINDS, City, Locality
from apps.catalog.selectors import CityRef, LocalityRef, get_city
from apps.kernel import audit
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError

NAME_MIN, NAME_MAX = 2, 80
_JOINERS = " '-"


def _clean_name(name, digits: bool = False) -> str:
    """NFC, single spaces; letters (and their combining marks), spaces, hyphens, apostrophes
    (and, for a locality, decimal digits: `DHA Phase 6`; never parentheses).

    The name must start with a letter and end with a letter, a digit (locality only) or a
    combining mark.
    """
    if not isinstance(name, str):
        raise DomainError("validation_error")
    name = " ".join(unicodedata.normalize("NFC", name).split())
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise DomainError("validation_error")
    for ch in name:
        cat = unicodedata.category(ch)
        if not (
            cat.startswith("L") or cat in ("Mn", "Mc") or ch in _JOINERS or (digits and cat == "Nd")
        ):
            raise DomainError("validation_error")
    if not unicodedata.category(name[0]).startswith("L") or name[-1] in _JOINERS:
        raise DomainError("validation_error")
    return name


def _name_taken(name: str) -> bool:
    return City.objects.annotate(lowered=Lower("name")).filter(lowered=name.lower()).exists()


def create_city(actor: Actor, name: str) -> CityRef:
    """Administrators add a city; the audit record is written in the same transaction."""
    require_admin(actor)
    name = _clean_name(name)
    with transaction.atomic():
        if _name_taken(name):
            raise DomainError("validation_error")
        try:
            with transaction.atomic():
                city = City.objects.create(name=name)
        except IntegrityError:  # lost a race to the unique constraint
            raise DomainError("validation_error") from None
        audit.record(actor, "city.created", "city", city.id)
    return CityRef(id=city.id, name=city.name)


def _locality_taken(city_id, name: str) -> bool:
    return (
        Locality.objects.filter(city_id=city_id)
        .annotate(lowered=Lower("name"))
        .filter(lowered=name.lower())
        .exists()
    )


def create_locality(actor: Actor, city_id, name: str, kind: str) -> LocalityRef:
    """Administrators add an area or society to an active city; audited as `locality.created`
    in the same transaction. An unknown or inactive city, a bad name or kind and a name the city
    already has (ignoring case) are `validation_error`."""
    require_admin(actor)
    name = _clean_name(name, digits=True)
    if not isinstance(kind, str) or kind not in LOCALITY_KINDS:
        raise DomainError("validation_error")
    city = get_city(city_id)
    if city is None:
        raise DomainError("validation_error")
    with transaction.atomic():
        if _locality_taken(city.id, name):
            raise DomainError("validation_error")
        try:
            with transaction.atomic():
                row = Locality.objects.create(city_id=city.id, name=name, kind=kind)
        except IntegrityError:  # lost a race to the unique constraint
            raise DomainError("validation_error") from None
        audit.record(actor, "locality.created", "locality", row.id)
    return LocalityRef(id=row.id, name=row.name, kind=row.kind, city_id=row.city_id)
