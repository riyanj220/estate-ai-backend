"""Removal of stored estimates (AD-23, PRD 8.3).

A deleted Listing's estimates go at once, on `listing.deleted` (`subscribers`). The retention job
repairs what that missed and expires estimates of entered attributes after 90 days."""

from datetime import datetime, timedelta

from django.db.models import Q

from apps.kernel.actor import Actor
from apps.listings import selectors as listings
from apps.valuation.models import PriceEstimate

AD_HOC_RETENTION = timedelta(days=90)


def delete_for_listing(listing_id) -> int:
    deleted, _ = PriceEstimate.objects.filter(listing_id=listing_id).delete()
    return deleted


def purge_estimates(now: datetime, dry_run: bool = False) -> int:
    stored = set(
        PriceEstimate.objects.exclude(listing_id=None)
        .values_list("listing_id", flat=True)
        .distinct()
    )
    gone = stored - listings.existing_listing_ids(Actor.system("retention"), stored)
    rows = PriceEstimate.objects.filter(
        Q(listing_id__in=gone) | Q(listing_id=None, created_at__lt=now - AD_HOC_RETENTION)
    )
    if dry_run:
        return rows.count()
    deleted, _ = rows.delete()
    return deleted
