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
from apps.listings.fields import (
    COMMERCIAL_FIELDS,
    PLOT_FIELDS,
    RESIDENTIAL_FIELDS,
    TERMS_FIELDS,
)
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


# The sale and rent terms added in Story 2.6; their CHECKs are generated like the details tables'.
# (The price, basis and period constraints keep their original names.)
NEW_TERMS_FIELDS = tuple(
    name for name in TERMS_FIELDS if name not in ("asking_price", "price_basis", "rental_period")
)


def terms_constraints() -> list[models.CheckConstraint]:
    """Price, enum, range and date-window CHECKs of the terms, generated from the current
    registry. A NULL always passes: unanswered is allowed."""
    price, basis, period = _def("asking_price"), _def("price_basis"), _def("rental_period")
    return [
        *_terms_price_constraints(price, basis, period),
        *_detail_constraints("listings_terms", NEW_TERMS_FIELDS),
    ]


def _terms_price_constraints(price, basis, period) -> list[models.CheckConstraint]:
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


def _detail_constraints(prefix: str, names) -> list[models.CheckConstraint]:
    """Bound and enum CHECKs of a details table, generated from the current registry.

    Integers and decimals within their bounds, enums within their values (a by-Subtype map is
    narrower than the column can know, so the column allows the union); booleans need none.
    A NULL always passes: unknown is allowed.
    """
    constraints = []
    for name in names:
        definition = _def(name)
        if definition.type == "enum":
            inside = models.Q(**{f"{name}__in": list(definition.allowed_values)})
            suffix = "valid"
        elif definition.type in ("integer", "decimal", "money", "date"):
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
                name=f"{prefix}_{name}_{suffix}",
            )
        )
    return constraints


def residential_constraints() -> list[models.CheckConstraint]:
    """Bound and enum CHECKs of the unit details, generated from the current registry, plus the
    one cross-field rule: a floor within its building (any Subtype where both apply)."""
    constraints = _detail_constraints("listings_res", RESIDENTIAL_FIELDS)
    constraints.append(
        models.CheckConstraint(
            condition=models.Q(floor_number__isnull=True)
            | models.Q(building_floors__isnull=True)
            | models.Q(floor_number__lte=models.F("building_floors")),
            name="listings_res_floor_within_building",
        )
    )
    return constraints


def plot_constraints() -> list[models.CheckConstraint]:
    """Bound and enum CHECKs of the plot details, generated from the current registry."""
    return _detail_constraints("listings_plot", PLOT_FIELDS)


def commercial_constraints() -> list[models.CheckConstraint]:
    """Bound and enum CHECKs of the commercial details, generated from the current registry."""
    return _detail_constraints("listings_com", COMMERCIAL_FIELDS)


class PropertyListing(models.Model):
    """The one canonical Listing. Written only by `listings.services`; never leaves the app."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_account_id = models.UUIDField()  # plain column: no key into identity
    status = models.CharField(max_length=16, choices=_choices(s.value for s in ListingStatus))
    # Set by moderation (a later epic); fixtures only until then. A restricted Listing is never
    # public, whatever its status (`listings.policy`).
    restricted = models.BooleanField(default=False)
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
            # the public predicate (`listings.policy.public_q`): published, not restricted, by owner
            models.Index(
                fields=["status", "restricted", "owner_account_id"], name="listings_public_idx"
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
    """Price and the sale and rent terms of a Listing (one row, created with the Draft). Money is
    NUMERIC, never a float. Every column is nullable: unanswered is NULL, never 0 or false, and a
    date is a plain calendar date (SQL DATE), never a timestamp."""

    listing = models.OneToOneField(PropertyListing, on_delete=models.CASCADE, related_name="terms")
    asking_price = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    price_basis = models.CharField(max_length=32, null=True)
    rental_period = models.CharField(max_length=32, null=True)
    price_negotiable = models.BooleanField(null=True)
    possession_availability = models.CharField(max_length=32, null=True)  # sale only
    security_deposit = models.DecimalField(max_digits=14, decimal_places=2, null=True)  # rent
    availability_date = models.DateField(null=True)  # rent only
    advance_rent = models.DecimalField(max_digits=14, decimal_places=2, null=True)  # rent only
    minimum_lease_months = models.SmallIntegerField(null=True)  # rent only
    notice_period_days = models.SmallIntegerField(null=True)  # rent only
    utility_electricity_included = models.BooleanField(null=True)  # rent only
    utility_gas_included = models.BooleanField(null=True)  # rent only
    utility_water_included = models.BooleanField(null=True)  # rent only
    utility_internet_included = models.BooleanField(null=True)  # rent only
    pets_allowed = models.BooleanField(null=True)  # rent only
    occupant_restrictions = models.CharField(max_length=32, null=True)  # rent only
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
    """The unit details of a Listing (one row, created lazily on the first non-null value): the
    House and Apartment set, the details Shops and Offices share with them (condition, floors,
    parking, ...) and the utilities shared with plots. The model keeps its name. Every column is
    nullable: unanswered is NULL, never 0 or false."""

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
    electricity = models.BooleanField(null=True)
    water = models.BooleanField(null=True)
    gas = models.BooleanField(null=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = residential_constraints()


class ListingPlot(models.Model):
    """Plot and Plot File details of a Listing (one row, created lazily on the first non-null
    value). Every column is nullable: unanswered is NULL, never 0 or false. Possession, balloting,
    development, approval and plot file status are the seller's own claims (registry flag
    `seller_declared`)."""

    listing = models.OneToOneField(PropertyListing, on_delete=models.CASCADE, related_name="plot")
    plot_width = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    plot_depth = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    possession_status = models.CharField(max_length=32, null=True)
    corner_plot = models.BooleanField(null=True)
    road_width_ft = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    balloting_status = models.CharField(max_length=32, null=True)
    sewerage = models.BooleanField(null=True)
    development_status = models.CharField(max_length=32, null=True)
    approval_authority = models.CharField(max_length=32, null=True)
    park_facing = models.BooleanField(null=True)
    main_boulevard = models.BooleanField(null=True)
    boundary_wall = models.BooleanField(null=True)
    orientation = models.CharField(max_length=32, null=True)
    plot_file_status = models.CharField(max_length=32, null=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = plot_constraints()


class ListingCommercial(models.Model):
    """Shop and Office details of a Listing (one row, created lazily on the first non-null
    value). Every column is nullable: unanswered is NULL, never 0 or false."""

    listing = models.OneToOneField(
        PropertyListing, on_delete=models.CASCADE, related_name="commercial"
    )
    business_use = models.CharField(max_length=32, null=True)
    frontage_ft = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    road_access = models.CharField(max_length=32, null=True)
    ceiling_height_ft = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    power_load_kw = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    three_phase_electricity = models.BooleanField(null=True)
    conference_rooms = models.IntegerField(null=True)
    business_centre_facilities = models.BooleanField(null=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = commercial_constraints()


MAX_IMAGES = 10


class ListingImage(models.Model):
    """An image attached to a Listing (Stories 2.9 and 2.10). The file is a `kernel` media asset,
    referenced by a plain UUID (no foreign key into the kernel); the number of images is limited
    by the service under the Listing row lock, not by the database.

    `position` is 0-based and contiguous per Listing (0..n-1): a service-maintained invariant
    (the database only forbids duplicates, and that check is deferred to commit so a reorder can
    rewrite positions in any order). `is_cover` marks the cover: at most one per Listing (a
    partial unique index), and the service keeps exactly one while the Listing has images."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing = models.ForeignKey(PropertyListing, on_delete=models.CASCADE, related_name="images")
    asset_id = models.UUIDField(unique=True)
    position = models.PositiveIntegerField()
    is_cover = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["listing", "position"],
                name="listings_image_position_unique",
                deferrable=models.Deferrable.DEFERRED,
            ),
            models.UniqueConstraint(
                fields=["listing"],
                condition=models.Q(is_cover=True),
                name="listings_image_one_cover",
            ),
            models.CheckConstraint(
                condition=models.Q(position__gte=0), name="listings_image_position_gte_0"
            ),
        ]
