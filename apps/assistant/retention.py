"""Removal of Assistant history (AD-23). A deleted Draft's history goes at once, on
`listing.deleted` (`subscribers`); the retention job repairs what that missed."""

from datetime import datetime

from apps.assistant.models import AssistantSession, AssistantTurn
from apps.kernel.actor import Actor
from apps.listings import selectors as listings


def delete_for_listing(listing_id) -> int:
    """Sessions and their turns; returns the number of rows deleted."""
    deleted, _ = AssistantSession.objects.filter(listing_id=listing_id).delete()
    return deleted


def purge_orphaned(now: datetime, dry_run: bool = False) -> int:
    stored = set(AssistantSession.objects.values_list("listing_id", flat=True).distinct())
    gone = stored - listings.existing_listing_ids(Actor.system("retention"), stored)
    sessions = AssistantSession.objects.filter(listing_id__in=gone)
    if dry_run:
        return sessions.count() + AssistantTurn.objects.filter(session__in=sessions).count()
    deleted, _ = sessions.delete()
    return deleted
