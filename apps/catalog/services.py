"""Write path for catalog data. Only this module (and data migrations) writes catalog tables."""

import unicodedata

from django.db import IntegrityError, transaction
from django.db.models.functions import Lower

from apps.catalog.models import City
from apps.catalog.selectors import CityRef
from apps.kernel import audit
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError

NAME_MIN, NAME_MAX = 2, 80
_JOINERS = " '-"


def _clean_name(name) -> str:
    """NFC, single spaces; letters (and their combining marks), spaces, hyphens, apostrophes.

    The name must start with a letter and end with a letter or a combining mark.
    """
    if not isinstance(name, str):
        raise DomainError("validation_error")
    name = " ".join(unicodedata.normalize("NFC", name).split())
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise DomainError("validation_error")
    for ch in name:
        cat = unicodedata.category(ch)
        if not (cat.startswith("L") or cat in ("Mn", "Mc") or ch in _JOINERS):
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
