import uuid
from functools import reduce
from operator import or_

from django.db import models
from django.db.models.functions import Length
from django.db.models.lookups import GreaterThanOrEqual, LessThanOrEqual
from django.utils import timezone

from apps.catalog import registry
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
