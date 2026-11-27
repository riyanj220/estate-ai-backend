"""Removal of Assistant history (AD-23). A deleted Draft's history goes at once, on
`listing.deleted` (`subscribers`); the retention job repairs what that missed. The history of a
Listing goes 90 days after its first Publication (PRD 8.3, Story 10.12)."""

from datetime import datetime, timedelta

from apps.assistant.models import AssistantSession, AssistantTurn
from apps.kernel.actor import Actor
from apps.listings import selectors as listings

AFTER_PUBLICATION = timedelta(days=90)


def delete_for_listing(listing_id) -> int:
    """Sessions and their turns; returns the number of rows deleted."""
    deleted, _ = AssistantSession.objects.filter(listing_id=listing_id).delete()
    return deleted


def _stored_listing_ids() -> set:
    return set(AssistantSession.objects.values_list("listing_id", flat=True).distinct())


def _purge_for(listing_ids, dry_run: bool) -> int:
    sessions = AssistantSession.objects.filter(listing_id__in=listing_ids)
    if dry_run:
        return sessions.count() + AssistantTurn.objects.filter(session__in=sessions).count()
    deleted, _ = sessions.delete()
    return deleted


def purge_orphaned(now: datetime, dry_run: bool = False) -> int:
    stored = _stored_listing_ids()
    gone = stored - listings.existing_listing_ids(Actor.system("retention"), stored)
    return _purge_for(gone, dry_run)


def purge_published(now: datetime, dry_run: bool = False) -> int:
    old = listings.listing_ids_first_published_before(
        Actor.system("retention"), _stored_listing_ids(), now - AFTER_PUBLICATION
    )
    return _purge_for(old, dry_run)
