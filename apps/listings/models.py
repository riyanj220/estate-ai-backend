import uuid
from functools import reduce
from operator import or_

from django.db import models
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
