"""Read path for catalog data. Returns plain immutable values, never model instances."""

import uuid
from dataclasses import dataclass

from django.db.models.functions import Lower

from apps.catalog.models import City


@dataclass(frozen=True)
class CityRef:
    id: uuid.UUID
    name: str


def list_cities() -> tuple[CityRef, ...]:
    """Active cities ordered by name, case-insensitively."""
    rows = City.objects.filter(is_active=True).order_by(Lower("name"), "name", "id")
    return tuple(CityRef(id=c.id, name=c.name) for c in rows.only("id", "name"))


def get_city(city_id) -> CityRef | None:
    """An active city by id; inactive, unknown or malformed ids give None."""
    if not isinstance(city_id, uuid.UUID):
        try:
            city_id = uuid.UUID(str(city_id))
        except ValueError:
            return None
    city = City.objects.filter(id=city_id, is_active=True).only("id", "name").first()
    return CityRef(id=city.id, name=city.name) if city else None
