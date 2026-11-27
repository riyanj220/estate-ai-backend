"""Retention jobs of Drafts (Story 2.13, AD-23), run by `run_retention` (registered in `apps.py`).

`draft_notice` tells the owner of a Draft with 180 days of no activity that it will be deleted in
30 days; `draft_expiry` deletes it after that unless the owner opened or edited it since. Both
select candidate ids with `listings.selectors` and hand each one to a `listings.services` function
that decides again under the locks, in its own savepoint: one bad Listing is logged by exception
class only (never a title, address or id) and never stops the others. Published and other
non-draft Listings are never candidates.
"""

import logging
from datetime import datetime

from django.db import transaction

from apps.kernel.actor import Actor
from apps.listings import selectors, services
from apps.listings.models import ListingViewBucket

logger = logging.getLogger("estate.retention")


def _pages(select, now: datetime):
    """Keyset pages of candidate ids across the whole run (at most `RETENTION_MAX_PAGES`), so a
    Listing that always fails or is always skipped never blocks the ones after it."""
    after = None
    for _ in range(selectors.RETENTION_MAX_PAGES):
        ids = select(now, after)
        if not ids:
            return
        yield ids
        after = ids[-1]


def _each(ids, now: datetime, act) -> list:
    system = Actor.system("retention")
    outcomes = []
    for key in ids:
        try:
            with transaction.atomic():
                outcomes.append(act(system, key, now))
        except Exception as exc:
            logger.error(
                "draft_retention_failed",
                extra={"event": "draft_retention_failed", "exception_class": type(exc).__name__},
            )
    return outcomes


def draft_notice(now: datetime, dry_run: bool) -> int:
    """Notice idle Drafts. Returns how many were noticed (would be, in a dry run)."""
    total = 0
    for ids in _pages(selectors.idle_draft_ids, now):
        total += (
            len(ids)
            if dry_run
            else sum(1 for done in _each(ids, now, services.notice_idle_draft) if done)
        )
    return total


def draft_expiry(now: datetime, dry_run: bool) -> int:
    """Delete the Drafts whose time has come; clear the notice of those that were opened or edited
    since; give a fresh grace period to those whose notice may not have arrived or whose date
    passed long ago. Returns how many were deleted (would be, in a dry run)."""
    total = 0
    for ids in _pages(selectors.due_draft_ids, now):
        total += (
            selectors.deletable_now(ids, now)
            if dry_run
            else sum(1 for done in _each(ids, now, services.expire_idle_draft) if done == "deleted")
        )
    return total


def view_buckets(now: datetime, dry_run: bool) -> int:
    """Delete the expired view-counting buckets (Story 8.2)."""
    expired = ListingViewBucket.objects.filter(expires_at__lte=now)
    return expired.count() if dry_run else expired.delete()[0]
