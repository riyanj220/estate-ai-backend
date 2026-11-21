import uuid
from decimal import Decimal
from functools import reduce
from operator import or_

from django.db import models
from django.db.models.functions import Length
from django.db.models.lookups import GreaterThanOrEqual, LessThanOrEqual
from django.utils import timezone

from apps.catalog import registry
from apps.listings import area
from apps.listings.fields import RESIDENTIAL_FIELDS
from apps.listings.lifecycle import ListingStatus, Origin


def _choices(values):
    return [(v, v) for v in values]


def _taxonomy_pairs() -> models.Q:
    """A subtype must belong to its category (the registry taxonomy, frozen into the migration)."""
    return reduce(
        or_,
        (models.Q(category=c.id, subtype__in=list(c.subtypes)) for c in registry.categories()),
    )


def _def(field_id: str) -> registry.FieldDef:
    found = registry.field(field_id)
    if found is None:  # a registry without the editable fields cannot start
        raise RuntimeError(f"registry field {field_id} is missing")
    return found


_TITLE = _def("title")


def text_constraints() -> list[models.CheckConstraint]:
    """Length CHECKs of title and description, generated from the current registry."""
    title, description = _def("title"), _def("description")
    return [
        models.CheckConstraint(
            condition=models.Q(title__isnull=True)
            | (
                models.Q(GreaterThanOrEqual(Length("title"), title.min_length))
                & models.Q(LessThanOrEqual(Length("title"), title.max_length))
            ),
            name="listings_title_length",
        ),
        models.CheckConstraint(
            condition=models.Q(description__isnull=True)
            | models.Q(LessThanOrEqual(Length("description"), description.max_length)),
            name="listings_description_length",
        ),
    ]


def terms_constraints() -> list[models.CheckConstraint]:
    """Price range and enum CHECKs of the terms, generated from the current registry."""
    price, basis, period = _def("asking_price"), _def("price_basis"), _def("rental_period")
    return [
        models.CheckConstraint(
            condition=models.Q(asking_price__isnull=True)
            | models.Q(asking_price__gte=price.minimum, asking_price__lte=price.maximum),
            name="listings_terms_price_range",
        ),
        models.CheckConstraint(
            condition=models.Q(price_basis__isnull=True)
            | models.Q(price_basis__in=list(basis.allowed_values)),
            name="listings_terms_basis_valid",
        ),
        models.CheckConstraint(
            condition=models.Q(rental_period__isnull=True)
            | models.Q(rental_period__in=list(period.allowed_values)),
            name="listings_terms_period_valid",
        ),
    ]


def area_constraints() -> list[models.CheckConstraint]:
    """Bounds, enum and consistency CHECKs of the area, generated from the current registry."""
    value, unit, basis = _def("area_value"), _def("area_unit"), _def("marla_basis")
    sqft = _def("area_sqft")
    # `IN` on a NULL unit is NULL, which a CHECK lets through: name the NULL case explicitly.
    needs_basis = models.Q(area_unit__in=list(area.MARLA_UNITS), area_unit__isnull=False)
    computable = (
        models.Q(area_value__isnull=False)
        & models.Q(area_unit__isnull=False)
        & (~needs_basis | models.Q(marla_basis__isnull=False))
    )
    return [
        models.CheckConstraint(
            condition=models.Q(area_value__isnull=True)
            | models.Q(area_value__gte=value.minimum, area_value__lte=value.maximum),
            name="listings_area_value_range",
        ),
        models.CheckConstraint(
            condition=models.Q(area_unit__isnull=True)
            | models.Q(area_unit__in=list(unit.allowed_values)),
            name="listings_area_unit_valid",
        ),
        models.CheckConstraint(
            condition=models.Q(marla_basis__isnull=True)
            | models.Q(marla_basis__in=[Decimal(v) for v in basis.allowed_values]),
            name="listings_area_basis_valid",
        ),
        models.CheckConstraint(
            condition=models.Q(marla_basis__isnull=True) | needs_basis,
            name="listings_area_basis_applies",
        ),
        models.CheckConstraint(
            condition=models.Q(area_sqft__isnull=True)
            | (models.Q(area_sqft__gte=sqft.minimum, area_sqft__lte=sqft.maximum) & computable),
            name="listings_area_sqft_consistent",
        ),
    ]


def residential_constraints() -> list[models.CheckConstraint]:
    """Bound and enum CHECKs of the residential details, generated from the current registry.

    Integers and decimals within their bounds, enums within their values; booleans need none.
    A NULL always passes: unknown is allowed. Plus the one cross-field rule, an apartment's floor
    within its building.
    """
    constraints = []
    for name in RESIDENTIAL_FIELDS:
        definition = _def(name)
        if definition.type == "enum":
            inside = models.Q(**{f"{name}__in": list(definition.allowed_values)})
            suffix = "valid"
        elif definition.type in ("integer", "decimal", "money"):
            low, high = definition.minimum, definition.maximum
            if definition.type == "integer":
                low, high = int(low), int(high)
            inside = models.Q(**{f"{name}__gte": low, f"{name}__lte": high})
            suffix = "range"
        else:
            continue
        constraints.append(
            models.CheckConstraint(
                condition=models.Q(**{f"{name}__isnull": True}) | inside,
                name=f"listings_res_{name}_{suffix}",
            )
        )
    constraints.append(
        models.CheckConstraint(
            condition=models.Q(floor_number__isnull=True)
            | models.Q(building_floors__isnull=True)
            | models.Q(floor_number__lte=models.F("building_floors")),
            name="listings_res_floor_within_building",
        )
    )
    return constraints


class PropertyListing(models.Model):
    """The one canonical Listing. Written only by `listings.services`; never leaves the app."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_account_id = models.UUIDField()  # plain column: no key into identity
    status = models.CharField(max_length=16, choices=_choices(s.value for s in ListingStatus))
    revision = models.PositiveIntegerField(default=1)
    registry_version = models.PositiveIntegerField()
    purpose = models.CharField(max_length=32)
    category = models.CharField(max_length=32)
    subtype = models.CharField(max_length=32)
    title = models.CharField(max_length=_TITLE.max_length, null=True)  # empty is null
    description = models.TextField(null=True)  # empty is null
    # registry field id -> {"origin": "manual"|"ai", "confirmed": bool}, set fields only
    provenance = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            # the unfiltered own list, and the status-filtered one (newest first, id tiebreak)
            models.Index(
                fields=["owner_account_id", "-created_at", "-id"], name="listings_owner_list_idx"
            ),
            models.Index(
                fields=["owner_account_id", "status", "-created_at", "-id"],
                name="listings_owner_status_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=[s.value for s in ListingStatus]),
                name="listings_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(revision__gte=1), name="listings_revision_ge1"
            ),
            models.CheckConstraint(
                condition=models.Q(registry_version__gte=1), name="listings_regver_ge1"
            ),
            models.CheckConstraint(
                condition=models.Q(purpose__in=list(registry.purposes())),
                name="listings_purpose_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(category__in=[c.id for c in registry.categories()]),
                name="listings_category_valid",
            ),
            models.CheckConstraint(
                condition=_taxonomy_pairs(), name="listings_subtype_in_category"
            ),
            *text_constraints(),
        ]


class ListingRevision(models.Model):
    """One row per revision of a Listing: which registry fields were set and by whom."""

    listing = models.ForeignKey(PropertyListing, on_delete=models.CASCADE, related_name="revisions")
    revision = models.PositiveIntegerField()
    origin = models.CharField(max_length=16, choices=_choices(o.value for o in Origin))
    actor_account_id = models.UUIDField()
    changes = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["listing", "revision"], name="listings_revision_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(origin__in=[o.value for o in Origin]),
                name="listings_revision_origin_valid",
            ),
        ]


class ListingTerms(models.Model):
    """Price data of a Listing (one row, created lazily). Money is NUMERIC, never a float."""

    listing = models.OneToOneField(PropertyListing, on_delete=models.CASCADE, related_name="terms")
    asking_price = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    price_basis = models.CharField(max_length=32, null=True)
    rental_period = models.CharField(max_length=32, null=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = terms_constraints()


class ListingArea(models.Model):
    """Area of a Listing (one row, created lazily). `area_sqft` is derived by the service from
    the value, unit and marla basis (`listings.area`) and is never accepted from a client."""

    listing = models.OneToOneField(PropertyListing, on_delete=models.CASCADE, related_name="area")
    area_value = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    area_unit = models.CharField(max_length=16, null=True)
    marla_basis = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    area_sqft = models.DecimalField(max_digits=20, decimal_places=4, null=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = area_constraints()


class ListingResidential(models.Model):
    """House and Apartment details of a Listing (one row, created lazily on the first non-null
    value). Every column is nullable: unanswered is NULL, never 0 or false."""

    listing = models.OneToOneField(
        PropertyListing, on_delete=models.CASCADE, related_name="residential"
    )
    bedrooms = models.IntegerField(null=True)
    bathrooms = models.IntegerField(null=True)
    construction_year = models.IntegerField(null=True)
    condition = models.CharField(max_length=32, null=True)
    furnishing = models.CharField(max_length=32, null=True)
    parking_spaces = models.IntegerField(null=True)
    occupancy_status = models.CharField(max_length=32, null=True)
    covered_area_sqft = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    total_floors = models.IntegerField(null=True)
    kitchens = models.IntegerField(null=True)
    construction_status = models.CharField(max_length=32, null=True)
    drawing_rooms = models.IntegerField(null=True)
    dining_rooms = models.IntegerField(null=True)
    tv_lounges = models.IntegerField(null=True)
    study_rooms = models.IntegerField(null=True)
    prayer_rooms = models.IntegerField(null=True)
    store_rooms = models.IntegerField(null=True)
    laundry_rooms = models.IntegerField(null=True)
    powder_rooms = models.IntegerField(null=True)
    servant_quarters = models.IntegerField(null=True)
    basement = models.BooleanField(null=True)
    floor_number = models.IntegerField(null=True)
    building_floors = models.IntegerField(null=True)
    lift = models.BooleanField(null=True)
    backup_electricity = models.BooleanField(null=True)
    building_security = models.BooleanField(null=True)
    balcony_or_terrace = models.BooleanField(null=True)
    building_lobby = models.BooleanField(null=True)
    intercom = models.BooleanField(null=True)
    maintenance_charges = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = residential_constraints()
