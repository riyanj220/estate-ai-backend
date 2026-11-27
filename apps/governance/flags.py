"""Advisory flags for administrators (Story 9.6, FR-66). Two fixed rules, no scoring and no model:

- duplicate: two Published Listings of one owner with the same purpose, category, locality and
  area, and a price within 2% of each other or a title at least 90% alike;
- suspicious: a Listing with open Reports from two different reporters made within 7 days.

Flags are derived data: a run adds the rows that now hold and removes those that no longer do.
Nothing here changes a Listing or a Report.
"""

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from itertools import combinations

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.governance.models import FLAG_KINDS, OPEN_REPORT_STATUSES, ListingFlag, Report
from apps.identity import selectors as identity
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError
from apps.listings import selectors as listings

KINDS = tuple(FLAG_KINDS)
PAGE_SIZE = 20
PRICE_TOLERANCE = Decimal("0.02")
TITLE_SIMILARITY = 0.9
REPORT_WINDOW = timedelta(days=7)
_SYSTEM = Actor.system("governance")


class InvalidCursor(Exception):
    pass


@dataclass(frozen=True)
class FlagRow:
    id: uuid.UUID
    kind: str
    flagged_at: datetime
    listing: listings.AdminListingRow
    other_listing: listings.AdminListingRow | None
    matched: tuple[str, ...]  # duplicate: `price`, `title`
    reporter_count: int  # suspicious


@dataclass(frozen=True)
class FlagPage:
    items: list[FlagRow]
    next_cursor: str | None


def _title_key(title: str | None) -> str:
    return " ".join(re.findall(r"[^\W_]+", (title or "").casefold()))


def match(a: listings.DuplicateCandidate, b: listings.DuplicateCandidate) -> tuple[bool, bool]:
    """(near-identical price, near-identical title) for two Listings of one owner; both False
    when they are not the same purpose, category, locality and area."""
    same = ("owner_account_id", "purpose", "category", "locality", "area_sqft")
    if any(getattr(a, name) != getattr(b, name) for name in same):
        return False, False
    price = (
        a.price is not None
        and b.price is not None
        and abs(a.price - b.price) <= max(a.price, b.price) * PRICE_TOLERANCE
    )
    first, second = _title_key(a.title), _title_key(b.title)
    title = bool(first and second) and (
        SequenceMatcher(None, first, second, autojunk=False).ratio() >= TITLE_SIMILARITY
    )
    return price, title


def _pairs(candidates) -> dict[tuple[uuid.UUID, uuid.UUID], tuple[bool, bool]]:
    """(newer id, older id) -> what matched. `candidates` are oldest first."""
    groups = defaultdict(list)
    for item in candidates:
        groups[
            (item.owner_account_id, item.purpose, item.category, item.locality, item.area_sqft)
        ].append(item)
    found = {}
    for group in groups.values():
        for older, newer in combinations(group, 2):
            price, title = match(older, newer)
            if price or title:
                found[(newer.id, older.id)] = (price, title)
    return found


def _sync_duplicates(found, existing) -> int:
    """Make the `duplicate` rows in `existing` equal to `found`; returns the number added."""
    kept = set()
    for row in existing:
        key = (row.listing_id, row.other_listing_id)
        if key in found:
            kept.add(key)
            price, title = found[key]
            if (row.matched_price, row.matched_title) != (price, title):
                row.matched_price, row.matched_title = price, title
                row.save(update_fields=["matched_price", "matched_title"])
        else:
            row.delete()
    added = 0
    for (newer, older), (price, title) in found.items():
        if (newer, older) not in kept:
            _, created = ListingFlag.objects.get_or_create(
                kind="duplicate",
                listing_id=newer,
                other_listing_id=older,
                defaults={
                    "matched_price": price,
                    "matched_title": title,
                    "created_at": timezone.now(),
                },
            )
            added += created
    return added


def check_duplicates(listing_id) -> int:
    """The duplicate rule for one Listing (after `listing.published`)."""
    with transaction.atomic():
        candidates = listings.duplicate_candidates(_SYSTEM, same_owner_as=listing_id)
        found = {key: v for key, v in _pairs(candidates).items() if listing_id in key}
        existing = ListingFlag.objects.filter(kind="duplicate").filter(
            Q(listing_id=listing_id) | Q(other_listing_id=listing_id)
        )
        return _sync_duplicates(found, existing)


def delete_for_listing(listing_id) -> None:
    ListingFlag.objects.filter(Q(listing_id=listing_id) | Q(other_listing_id=listing_id)).delete()


def _reported_listings() -> dict[uuid.UUID, int]:
    """Listing id -> number of reporters with an open Report, for those the rule matches."""
    reports = defaultdict(list)
    rows = Report.objects.filter(target_type="listing", status__in=OPEN_REPORT_STATUSES)
    for target, reporter, at in rows.order_by("created_at", "id").values_list(
        "target_id", "reporter_account_id", "created_at"
    ):
        reports[target].append((reporter, at))
    found = {}
    for target, entries in reports.items():
        if any(
            first[0] != second[0] and second[1] - first[1] <= REPORT_WINDOW
            for first, second in combinations(entries, 2)
        ):
            found[target] = len({reporter for reporter, _ in entries})
    return found


def _sync_suspicious() -> int:
    found = _reported_listings()
    known = listings.existing_listing_ids(_SYSTEM, found)
    found = {key: count for key, count in found.items() if key in known}
    for row in ListingFlag.objects.filter(kind="suspicious"):
        if row.listing_id not in found:
            row.delete()
        elif row.reporter_count != found[row.listing_id]:
            row.reporter_count = found[row.listing_id]
            row.save(update_fields=["reporter_count"])
    added = 0
    for listing_id, count in found.items():
        _, created = ListingFlag.objects.get_or_create(
            kind="suspicious",
            listing_id=listing_id,
            defaults={"reporter_count": count, "created_at": timezone.now()},
        )
        added += created
    return added


def run_flags(actor: Actor) -> dict[str, int]:
    """Both rules over everything (the nightly `run_flags` command). System actor only. Returns
    the number of flags added per kind."""
    if actor.kind is not _SYSTEM.kind:
        raise DomainError("forbidden")
    with transaction.atomic():
        found = _pairs(listings.duplicate_candidates(_SYSTEM))
        duplicates = _sync_duplicates(found, ListingFlag.objects.filter(kind="duplicate"))
        suspicious = _sync_suspicious()
    return {"duplicate": duplicates, "suspicious": suspicious}


def flag_page(actor: Actor, kind: str | None = None, cursor: str | None = None) -> FlagPage:
    """The flags for review, newest first."""
    require_admin(actor)
    if kind is not None and kind not in KINDS:
        raise DomainError("validation_error")
    rows = ListingFlag.objects.order_by("-created_at", "-id")
    if kind is not None:
        rows = rows.filter(kind=kind)
    if cursor is not None:
        key = identity.parse_id(cursor)
        last = ListingFlag.objects.filter(id=key).first() if key else None
        if last is None:
            raise InvalidCursor
        rows = rows.filter(
            Q(created_at__lt=last.created_at) | Q(created_at=last.created_at, id__lt=last.id)
        )
    page = list(rows[: PAGE_SIZE + 1])
    items = page[:PAGE_SIZE]
    ids = {r.listing_id for r in items} | {r.other_listing_id for r in items if r.other_listing_id}
    shown = listings.admin_listing_rows(actor, ids)
    out = [
        FlagRow(
            r.id,
            r.kind,
            r.created_at,
            shown[r.listing_id],
            shown.get(r.other_listing_id),
            tuple(n for n, hit in (("price", r.matched_price), ("title", r.matched_title)) if hit),
            r.reporter_count,
        )
        for r in items
        if r.listing_id in shown  # a deleted Listing's row goes with the next run
    ]
    return FlagPage(out, str(items[-1].id) if len(page) > PAGE_SIZE else None)
