"""Read path for catalog data. Returns plain immutable values, never model instances."""

import uuid
from dataclasses import dataclass

from django.db.models import Case, IntegerField, Value, When
from django.db.models.functions import Lower

from apps.catalog.models import AMENITY_KINDS, Amenity, City, Locality


@dataclass(frozen=True)
class CityRef:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class LocalityRef:
    id: uuid.UUID
    name: str
    kind: str
    city_id: uuid.UUID


LOCALITY_LIMIT = 500  # the most localities one city's list returns (small, bounded, unpaginated)
_UUID_TEXT_MAX = 45  # `urn:uuid:` plus 36 characters; anything longer is not a uuid


def parse_uuid(value) -> uuid.UUID | None:
    """THE one reading of an id: a UUID, or text in any common form (any case, hyphenated or
    not, braced, `urn:uuid:`). Anything else, other types included, is None. Callers store and
    serve the canonical lowercase hyphenated form of the result."""
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str) or not value or len(value) > _UUID_TEXT_MAX:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


_as_uuid = parse_uuid


def list_cities() -> tuple[CityRef, ...]:
    """Active cities ordered by name, case-insensitively."""
    rows = City.objects.filter(is_active=True).order_by(Lower("name"), "name", "id")
    return tuple(CityRef(id=c.id, name=c.name) for c in rows.only("id", "name"))


def get_city(city_id) -> CityRef | None:
    """An active city by id; inactive, unknown or malformed ids give None."""
    key = _as_uuid(city_id)
    if key is None:
        return None
    city = City.objects.filter(id=key, is_active=True).only("id", "name").first()
    return CityRef(id=city.id, name=city.name) if city else None


def list_localities(city_id) -> tuple[LocalityRef, ...] | None:
    """Active localities of an active city, ordered by name (case-insensitively). None when the
    city is unknown, inactive or malformed (the endpoint answers `not_found`)."""
    city = get_city(city_id)
    if city is None:
        return None
    rows = Locality.objects.filter(city_id=city.id, is_active=True).order_by(
        Lower("name"), "name", "id"
    )[:LOCALITY_LIMIT]
    return tuple(
        LocalityRef(id=r.id, name=r.name, kind=r.kind, city_id=r.city_id)
        for r in rows.only("id", "name", "kind", "city_id")
    )


def get_locality(locality_id) -> LocalityRef | None:
    """An active locality of an active city by id, with its city id; unknown, inactive or
    malformed ids (and a locality of an inactive city) give None."""
    key = _as_uuid(locality_id)
    if key is None:
        return None
    row = (
        Locality.objects.filter(id=key, is_active=True, city__is_active=True)
        .only("id", "name", "kind", "city_id")
        .first()
    )
    if row is None:
        return None
    return LocalityRef(id=row.id, name=row.name, kind=row.kind, city_id=row.city_id)


def city_names(city_ids) -> dict[uuid.UUID, str]:
    """{id: name} of these cities in one query, deactivated ones included: a stored location
    keeps its name. Unknown ids and None are left out."""
    ids = {key for key in city_ids if key is not None}
    return dict(City.objects.filter(id__in=ids).values_list("id", "name")) if ids else {}


def locality_names(locality_ids) -> dict[uuid.UUID, str]:
    """{id: name} of these localities, as `city_names`."""
    ids = {key for key in locality_ids if key is not None}
    return dict(Locality.objects.filter(id__in=ids).values_list("id", "name")) if ids else {}


@dataclass(frozen=True)
class AmenityRef:
    code: str
    label: str
    kind: str
    group: str


def list_amenities() -> tuple[AmenityRef, ...]:
    """Active amenities: kind in the order on_property, community, nearby, then `sort_order`
    (then code, so the order is total). Small and bounded: unpaginated."""
    kind_order = Case(
        *(When(kind=kind, then=Value(i)) for i, kind in enumerate(AMENITY_KINDS)),
        output_field=IntegerField(),
    )
    rows = (
        Amenity.objects.filter(is_active=True)
        .annotate(kind_order=kind_order)
        .order_by("kind_order", "sort_order", "code")
        .only("code", "label", "kind", "group")
    )
    return tuple(AmenityRef(code=r.code, label=r.label, kind=r.kind, group=r.group) for r in rows)


def amenity_codes() -> frozenset[str]:
    """The codes of the active amenities: what a listing may newly declare."""
    return frozenset(Amenity.objects.filter(is_active=True).values_list("code", flat=True))
