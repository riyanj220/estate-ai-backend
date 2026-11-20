"""Read path for Listings. Returns plain immutable views, never model instances (AD-3).

Reads are owner-only: any other account, a Visitor and an administrator get `not_found`, so the
existence of a Listing is never confirmed.
"""

import base64
import binascii
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from django.db.models import Q

from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.listings.lifecycle import ListingStatus, visibility_of
from apps.listings.models import PropertyListing

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
_MIN_YEAR, _MAX_YEAR = 2000, 9000
_CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
# Registry field ids stored as columns of the Listing now; later stories add more.
_COLUMN_FIELDS = ("purpose", "category", "subtype")


@dataclass(frozen=True)
class ListingView:
    """The wire shape (AD-24). No owner or other internal column."""

    id: uuid.UUID
    status: str
    visibility: str
    revision: int
    registry_version: int
    fields: Mapping[str, object]  # flat registry ids; null for unknown
    media: tuple = ()
    amenities: tuple = ()


@dataclass(frozen=True)
class ListingPage:
    items: tuple[ListingView, ...]
    next_cursor: str | None


class InvalidCursor(ValueError):
    """A cursor that is malformed or was not issued by `list_owned`."""


def view_of(row: PropertyListing) -> ListingView:
    return ListingView(
        id=row.id,
        status=row.status,
        visibility=visibility_of(ListingStatus(row.status)).value,
        revision=row.revision,
        registry_version=row.registry_version,
        fields=MappingProxyType({name: getattr(row, name) for name in _COLUMN_FIELDS}),
    )


def _canonical_uuid(value) -> uuid.UUID | None:
    """Only the canonical lowercase hyphenated form (a UUID object also passes)."""
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    return parsed if str(parsed) == value else None


def _owner_actor(actor: Actor) -> bool:
    return actor.kind is ActorKind.USER and can(actor, Capability.VIEW_DRAFTS)


def get_owned_listing(actor: Actor, listing_id) -> ListingView:
    """The owner's own Listing; anything else is `not_found` (never forbidden)."""
    key = _canonical_uuid(listing_id)
    if key is None or not _owner_actor(actor):
        raise DomainError("not_found")
    row = PropertyListing.objects.filter(id=key, owner_account_id=actor.account_id).first()
    if row is None:
        raise DomainError("not_found")
    return view_of(row)


def encode_cursor(created_at: datetime, listing_id: uuid.UUID, status: str | None) -> str:
    raw = json.dumps(
        {"c": created_at.isoformat(), "i": str(listing_id), "s": status or ""},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, status: str | None) -> tuple[datetime, uuid.UUID]:
    """The position in a cursor issued by `list_owned` for the same status filter."""
    if not isinstance(cursor, str) or not _CURSOR.fullmatch(cursor):
        raise InvalidCursor
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(data, dict) or set(data) != {"c", "i", "s"}:
            raise InvalidCursor
        if not all(isinstance(data[k], str) for k in data):
            raise InvalidCursor
        created_at = datetime.fromisoformat(data["c"])
        listing_id = _canonical_uuid(data["i"])
        if created_at.tzinfo is None or listing_id is None or data["s"] != (status or ""):
            raise InvalidCursor
        created_at = created_at.astimezone(UTC)
        if not _MIN_YEAR <= created_at.year <= _MAX_YEAR:
            raise InvalidCursor
    except (binascii.Error, ValueError, TypeError, OverflowError, RecursionError):
        raise InvalidCursor from None
    return created_at, listing_id


def list_owned(
    actor: Actor, status: str | None = None, cursor: str | None = None, limit: int = DEFAULT_LIMIT
) -> ListingPage:
    """The caller's own Listings, newest first (creation time, then id), keyset-paginated.

    Visitors are `unauthenticated`; an account without `view_drafts` (an administrator,
    a Deactivated account) is `forbidden`. `status` must be a known status (ValueError otherwise);
    a bad `cursor` raises `InvalidCursor`.
    """
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, Capability.VIEW_DRAFTS):
        raise DomainError("forbidden")
    rows = PropertyListing.objects.filter(owner_account_id=actor.account_id)
    if status is not None:
        rows = rows.filter(status=ListingStatus(status).value)
    if cursor is not None:
        created_at, last_id = decode_cursor(cursor, status)
        rows = rows.filter(Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=last_id))
    size = max(1, min(int(limit), MAX_LIMIT))
    page = list(rows.order_by("-created_at", "-id")[: size + 1])
    more = len(page) > size
    page = page[:size]
    next_cursor = encode_cursor(page[-1].created_at, page[-1].id, status) if more else None
    return ListingPage(tuple(view_of(r) for r in page), next_cursor)
