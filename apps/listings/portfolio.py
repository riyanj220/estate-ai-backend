"""The Agency's own portfolio (Stories 8.1 and 8.2): totals, and its Listings filtered by status,
Category, location and creation date, each with its view count. Read only. Inquiry counts belong
to `messaging`; the API puts the two together."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db.models import Q, Sum

from apps.catalog import selectors as catalog
from apps.identity import selectors as identity
from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.listings import selectors
from apps.listings.models import ListingViewCount, PropertyListing
from apps.listings.quota import KARACHI

AGENCY = "agency"


@dataclass(frozen=True)
class Summary:
    total_listings: int
    by_status: dict[str, int]
    views_total: int


@dataclass(frozen=True)
class Criteria:
    """All optional; the ones given are combined with AND. The dates are Asia/Karachi calendar
    dates of the Listing's creation, both inclusive."""

    status: str | None = None
    category: str | None = None
    city: uuid.UUID | None = None
    locality: uuid.UUID | None = None
    created_from: date | None = None
    created_to: date | None = None


@dataclass(frozen=True)
class Row:
    id: uuid.UUID
    title: str | None
    status: str
    purpose: str
    category: str
    subtype: str
    city: selectors.PlaceView | None
    locality: selectors.PlaceView | None
    created_on: date  # the Asia/Karachi calendar date
    views: int


@dataclass(frozen=True)
class Page:
    items: tuple[Row, ...]
    next_cursor: str | None


def require_agency(actor: Actor) -> None:
    """Only an Agency account (Active, Email-unverified or Suspended). A Visitor is
    `unauthenticated`; every other account is `forbidden`."""
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, Capability.VIEW_DRAFTS):
        raise DomainError("forbidden")
    account = identity.get_account(actor.account_id)
    if account is None or account.type != AGENCY:
        raise DomainError("forbidden")


def summary(actor: Actor) -> Summary:
    require_agency(actor)
    by_status = selectors.owner_counts(actor)
    views = ListingViewCount.objects.filter(listing__owner_account_id=actor.account_id)
    return Summary(sum(by_status.values()), by_status, views.aggregate(n=Sum("views"))["n"] or 0)


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=KARACHI)


def _matching(criteria: Criteria) -> Q:
    wanted = Q()
    if criteria.status is not None:
        wanted &= Q(status=criteria.status)
    if criteria.category is not None:
        wanted &= Q(category=criteria.category)
    if criteria.city is not None:
        wanted &= Q(location__city=criteria.city)
    if criteria.locality is not None:
        wanted &= Q(location__locality=criteria.locality)
    if criteria.created_from is not None:
        wanted &= Q(created_at__gte=_day_start(criteria.created_from))
    if criteria.created_to is not None:
        wanted &= Q(created_at__lt=_day_start(criteria.created_to + timedelta(days=1)))
    return wanted


def listings(
    actor: Actor,
    criteria: Criteria,
    cursor: str | None = None,
    limit: int = selectors.DEFAULT_LIMIT,
) -> Page:
    """The Agency's own Listings that match, newest first, keyset-paginated. A cursor belongs to
    the filters it was issued for (`selectors.InvalidCursor` otherwise)."""
    require_agency(actor)
    tag = hashlib.sha256(repr(criteria).encode()).hexdigest()[:16]
    rows = PropertyListing.objects.select_related("location", "view_count").filter(
        _matching(criteria), owner_account_id=actor.account_id
    )
    if cursor is not None:
        at, last_id = selectors.decode_cursor(cursor, tag)
        rows = rows.filter(Q(created_at__lt=at) | Q(created_at=at, id__lt=last_id))
    size = max(1, min(int(limit), selectors.MAX_LIMIT))
    found = list(rows.order_by("-created_at", "-id")[: size + 1])
    page = found[:size]
    following = (
        selectors.encode_cursor(page[-1].created_at, page[-1].id, tag)
        if len(found) > size
        else None
    )
    places = {row.id: selectors.location_of(row) for row in page}
    cities = catalog.city_names(p.city for p in places.values() if p)
    localities = catalog.locality_names(p.locality for p in places.values() if p)

    def place(names, key):
        return selectors.PlaceView(key, names[key]) if key in names else None

    def views(row) -> int:
        counted = getattr(row, "view_count", None)  # no row until the first view
        return counted.views if counted else 0

    return Page(
        tuple(
            Row(
                id=row.id,
                title=row.title,
                status=row.status,
                purpose=row.purpose,
                category=row.category,
                subtype=row.subtype,
                city=place(cities, places[row.id].city) if places[row.id] else None,
                locality=place(localities, places[row.id].locality) if places[row.id] else None,
                created_on=row.created_at.astimezone(KARACHI).date(),
                views=views(row),
            )
            for row in page
        ),
        following,
    )
