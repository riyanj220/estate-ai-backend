"""Read path for Listings. Returns plain immutable views, never model instances (AD-3).

The owner-facing reads (`get_owned_listing`, `list_owned`) are owner-only: any other account, a
Visitor and an administrator get `not_found`, so the existence of a Listing is never confirmed.
Public reads start from `public_listings()` / `public_listing` / `listings.policy.can_view`, the
only implementations of visibility (AD-5, `docs/visibility.md`).
"""

import base64
import binascii
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from types import MappingProxyType

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import (
    Case,
    Count,
    DecimalField,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest

from apps.catalog import filters, registry
from apps.catalog import selectors as catalog
from apps.identity import selectors as identity
from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.kernel import media, notifications
from apps.kernel.actor import Actor, ActorKind, require_admin, require_signed_in
from apps.kernel.ai.redaction import KnownValues
from apps.kernel.errors import DomainError
from apps.kernel.privacy.reads import record_private_read
from apps.listings import lifecycle, policy
from apps.listings.area import basis_text
from apps.listings.fields import (
    ADDRESS_FIELDS,
    AMENITIES_FIELD,
    AREA_FIELDS,
    COMMERCIAL_FIELDS,
    DERIVED,
    DERIVED_FIELDS,
    IDENTITY_FIELDS,
    LISTING,
    LISTING_FIELDS,
    LOCATION_FIELDS,
    PLOT_FIELDS,
    RESIDENTIAL_FIELDS,
    STORAGE,
    TERMS_FIELDS,
)
from apps.listings.lifecycle import ListingStatus, Origin, visibility_of
from apps.listings.models import (
    ListingAmenity,
    ListingArea,
    ListingCommercial,
    ListingImage,
    ListingLocation,
    ListingPlot,
    ListingPrivateAddress,
    ListingResidential,
    ListingTerms,
    PropertyListing,
    SavedListing,
)
from apps.listings.quota import KARACHI

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
_MIN_YEAR, _MAX_YEAR = 2000, 9000
_CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_SEARCH_CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,300}$")
_COLUMN_FIELDS = (*IDENTITY_FIELDS, *LISTING_FIELDS)
# The detail tables joined into a read: the owner's resource also joins the PRIVATE address; the
# public projection never does.
PUBLIC_RELATED = ("terms", "area", "residential", "plot", "commercial", "location")
OWNER_RELATED = (*PUBLIC_RELATED, "private_address")
MONEY_FORMAT = "{:.2f}"  # money is a decimal string with two decimals on the wire


@dataclass(frozen=True, repr=False)
class ListingView:
    """The wire shape (AD-24). No owner or other internal column. `repr` and `str` redact every
    field that is not of class `public` (and any the registry does not define), so a private value
    never reaches a log line, a traceback or an assertion message through this object."""

    id: uuid.UUID
    status: str
    visibility: str
    revision: int
    registry_version: int
    fields: Mapping[str, object]  # flat registry ids; null for unknown
    # registry id -> {"origin", "confirmed"}, for fields that are set
    provenance: Mapping[str, Mapping[str, object]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    media: tuple = ()
    # Owner resource only (Story 2.17): moderation's flag and its short plain-text reason.
    restricted: bool = False
    restriction_reason: str | None = None

    def __repr__(self) -> str:
        shown = {
            name: value
            if (found := registry.field(name)) is not None and found.field_class == "public"
            else ("<private>" if value is not None else None)
            for name, value in self.fields.items()
        }
        return (
            f"ListingView(id={self.id!r}, status={self.status!r}, revision={self.revision!r}, "
            f"fields={shown!r})"
        )

    __str__ = __repr__


@dataclass(frozen=True)
class ImageView:
    """An attached image as the wire shows it (AD-24): `url` is the relative path of the media
    endpoint. Never the asset id, the storage key or the owner."""

    id: uuid.UUID
    position: int  # 0-based, contiguous per Listing
    is_cover: bool
    content_type: str
    width: int
    height: int
    bytes: int
    url: str


@dataclass(frozen=True)
class ServedImage:
    """The stored bytes of an image and the stored content type, for the media endpoint."""

    content_type: str
    data: bytes  # empty for a headers-only lookup
    size: int  # the stored size (the asset row's), also for a headers-only lookup
    public: bool = False  # the Listing is publicly visible right now (so the answer is cacheable)


def image_url(listing_id, image_id) -> str:
    """The relative path of the media endpoint for an image."""
    return f"/api/v1/listings/{listing_id}/images/{image_id}/file"


@dataclass(frozen=True)
class ListingPage:
    items: tuple[ListingView, ...]
    next_cursor: str | None


class InvalidCursor(ValueError):
    """A cursor that is malformed or was not issued for this list."""


def terms_of(row: PropertyListing) -> ListingTerms | None:
    """The Listing's terms row, or None when none was written yet (created lazily)."""
    try:
        return row.terms
    except ObjectDoesNotExist:
        return None


def area_of(row: PropertyListing) -> ListingArea | None:
    """The Listing's area row, or None when none was written yet (created lazily)."""
    try:
        return row.area
    except ObjectDoesNotExist:
        return None


def residential_of(row: PropertyListing) -> ListingResidential | None:
    """The Listing's residential details row, or None until a value is first saved."""
    try:
        return row.residential
    except ObjectDoesNotExist:
        return None


def plot_of(row: PropertyListing) -> ListingPlot | None:
    """The Listing's plot details row, or None until a value is first saved."""
    try:
        return row.plot
    except ObjectDoesNotExist:
        return None


def commercial_of(row: PropertyListing) -> ListingCommercial | None:
    """The Listing's commercial details row, or None until a value is first saved."""
    try:
        return row.commercial
    except ObjectDoesNotExist:
        return None


def location_of(row: PropertyListing) -> ListingLocation | None:
    """The Listing's public location row, or None until a value is first saved."""
    try:
        return row.location
    except ObjectDoesNotExist:
        return None


def address_of(row: PropertyListing) -> ListingPrivateAddress | None:
    """The Listing's PRIVATE address row, or None until a value is first saved. Only the owner's
    own reads and the edit service call this; no public path does (AD-5, AD-26)."""
    try:
        return row.private_address
    except ObjectDoesNotExist:
        return None


def amenities_of(row: PropertyListing) -> dict[str, str]:
    """The Listing's answered amenities as {code: state}; a code with no row is unanswered and is
    not in the map. Public: the map carries nothing but codes and states."""
    return amenities_by_listing([row.id])[row.id]


def amenities_by_listing(listing_ids) -> dict[uuid.UUID, dict[str, str]]:
    """The answered amenities of each Listing as {code: state} in code order, in one query."""
    ids = list(listing_ids)
    grouped: dict[uuid.UUID, dict[str, str]] = {key: {} for key in ids}
    rows = (
        ListingAmenity.objects.filter(listing_id__in=ids)
        .order_by("listing_id", "amenity_code")
        .values_list("listing_id", "amenity_code", "state")
    )
    for listing_id, code, state in rows:
        grouped[listing_id][code] = state
    return grouped


def clean_provenance(stored) -> dict[str, dict]:
    """Well-formed entries only: a malformed one is skipped, never an error for a read."""
    if not isinstance(stored, dict):
        return {}
    clean = {}
    for name, entry in stored.items():
        if (
            isinstance(name, str)
            and isinstance(entry, dict)
            and entry.get("origin") in {o.value for o in Origin}
            and isinstance(entry.get("confirmed"), bool)
        ):
            clean[name] = {"origin": entry["origin"], "confirmed": entry["confirmed"]}
    return clean


def field_text(name: str, value) -> str | int | bool | None:
    """A stored value as it goes on the wire and into revisions: money with two decimals, a
    decimal at its registry scale (`"10.0000"`), a date as `YYYY-MM-DD`, the marla basis as its
    enum string, integers and booleans as themselves, else text."""
    if value is None:
        return None
    if isinstance(value, bool | int):
        return value
    if isinstance(value, Mapping):  # the amenities map: codes and states only
        return dict(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()  # a calendar date `YYYY-MM-DD`, never a timestamp
    if name == "marla_basis" and isinstance(value, Decimal):
        return basis_text(value)
    definition = registry.field(name)
    if isinstance(value, Decimal) and definition is not None:
        if definition.type == "money":
            return MONEY_FORMAT.format(value)
        if definition.type == "decimal":
            return f"{value:.{definition.scale}f}"
    return value if isinstance(value, str) else str(value)


def view_of(
    row: PropertyListing,
    terms: ListingTerms | None = None,
    area: ListingArea | None = None,
    residential: ListingResidential | None = None,
    plot: ListingPlot | None = None,
    commercial: ListingCommercial | None = None,
    location: ListingLocation | None = None,
    address: ListingPrivateAddress | None = None,
    *,
    images: tuple[ImageView, ...] | None = None,
    public: bool | None = None,
    with_private: bool = False,
    amenities: Mapping[str, str] | None = None,
) -> ListingView:
    terms = terms if terms is not None else terms_of(row)
    area = area if area is not None else area_of(row)
    residential = residential if residential is not None else residential_of(row)
    plot = plot if plot is not None else plot_of(row)
    commercial = commercial if commercial is not None else commercial_of(row)
    location = location if location is not None else location_of(row)
    fields = {name: getattr(row, name) for name in _COLUMN_FIELDS}
    for name in TERMS_FIELDS:
        fields[name] = field_text(name, getattr(terms, name) if terms else None)
    for name in (*AREA_FIELDS, *DERIVED_FIELDS):
        fields[name] = field_text(name, getattr(area, name) if area else None)
    for name in RESIDENTIAL_FIELDS:
        fields[name] = field_text(name, getattr(residential, name) if residential else None)
    for name in PLOT_FIELDS:
        fields[name] = field_text(name, getattr(plot, name) if plot else None)
    for name in COMMERCIAL_FIELDS:
        fields[name] = field_text(name, getattr(commercial, name) if commercial else None)
    for name in LOCATION_FIELDS:
        fields[name] = field_text(name, getattr(location, name) if location else None)
    answered = dict(amenities) if amenities is not None else amenities_of(row)
    fields[AMENITIES_FIELD] = MappingProxyType(answered) if answered else None  # null: unanswered
    if with_private:  # the owner's resource only: the table is not even read otherwise
        address = address if address is not None else address_of(row)
        for name in ADDRESS_FIELDS:
            fields[name] = field_text(name, getattr(address, name) if address else None)
    provenance = {
        name: MappingProxyType(entry)
        for name, entry in clean_provenance(row.provenance).items()
        if name in fields and name not in DERIVED_FIELDS and fields[name] is not None
    }
    return ListingView(
        id=row.id,
        status=row.status,
        visibility=visibility_of(
            ListingStatus(row.status), public=policy.is_public(row) if public is None else public
        ).value,
        revision=row.revision,
        registry_version=row.registry_version,
        fields=MappingProxyType(fields),
        provenance=MappingProxyType(provenance),
        media=images if images is not None else media_by_listing([row.id])[row.id],
        restricted=row.restricted,
        restriction_reason=lifecycle.shown_reason(row),
    )


def live_image_count(listing_id) -> int:
    """Images of the Listing whose asset row exists (a link without one is a ghost: nothing can
    be shown, served or counted for it). The one rule of the image cap and of publication."""
    assets = list(
        ListingImage.objects.filter(listing_id=listing_id).values_list("asset_id", flat=True)
    )
    return len(media.assets_by_id(assets))


def media_by_listing(listing_ids) -> dict[uuid.UUID, tuple[ImageView, ...]]:
    """The images of each Listing in `position` order, in two queries. An image whose asset row
    is lost is left out (nothing can be shown or served for it)."""
    ids = list(listing_ids)
    links = list(ListingImage.objects.filter(listing_id__in=ids).order_by("listing_id", "position"))
    assets = media.assets_by_id(link.asset_id for link in links)
    grouped: dict[uuid.UUID, list[ImageView]] = {key: [] for key in ids}
    for link in links:
        asset = assets.get(link.asset_id)
        if asset is None:
            continue
        grouped[link.listing_id].append(
            ImageView(
                id=link.id,
                position=link.position,
                is_cover=link.is_cover,
                content_type=asset.content_type,
                width=asset.width,
                height=asset.height,
                bytes=asset.bytes,
                url=image_url(link.listing_id, link.id),
            )
        )
    return {key: tuple(items) for key, items in grouped.items()}


def canonical_uuid(value) -> uuid.UUID | None:
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
    key = canonical_uuid(listing_id)
    if key is None or not _owner_actor(actor):
        raise DomainError("not_found")
    row = (
        PropertyListing.objects.select_related(*OWNER_RELATED)
        .filter(id=key, owner_account_id=actor.account_id)
        .first()
    )
    if row is None:
        raise DomainError("not_found")
    return view_of(row, with_private=True)


def encode_cursor(created_at: datetime, listing_id: uuid.UUID, status: str | None) -> str:
    raw = json.dumps(
        {"c": created_at.isoformat(), "i": str(listing_id), "s": status or ""},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, status: str | None) -> tuple[datetime, uuid.UUID]:
    """The position in a cursor issued for the same list: `status` is the status filter of
    `list_owned`."""
    if not isinstance(cursor, str) or not _CURSOR.fullmatch(cursor):
        raise InvalidCursor
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(data, dict) or set(data) != {"c", "i", "s"}:
            raise InvalidCursor
        if not all(isinstance(data[k], str) for k in data):
            raise InvalidCursor
        created_at = datetime.fromisoformat(data["c"])
        listing_id = canonical_uuid(data["i"])
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
    rows = PropertyListing.objects.select_related(*OWNER_RELATED).filter(
        owner_account_id=actor.account_id
    )
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
    images = media_by_listing(r.id for r in page)
    public = policy.public_ids(r.id for r in page)
    answered = amenities_by_listing(r.id for r in page)
    return ListingPage(
        tuple(
            view_of(
                r,
                images=images[r.id],
                public=r.id in public,
                with_private=True,
                amenities=answered[r.id],
            )
            for r in page
        ),
        next_cursor,
    )


def owner_counts(actor: Actor) -> dict[str, int]:
    """How many Listings the caller's own account has in each status: every status is present
    (0 when none), in the order of `ListingStatus`. Same guard as `list_owned`: a Visitor is
    `unauthenticated`; an account without `view_drafts` (an administrator, a Deactivated
    account) is `forbidden`. A Suspended owner may read it."""
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, Capability.VIEW_DRAFTS):
        raise DomainError("forbidden")
    stored = dict(
        PropertyListing.objects.filter(owner_account_id=actor.account_id)
        .values_list("status")
        .annotate(total=Count("id"))
        .order_by()
    )
    return {s.value: stored.get(s.value, 0) for s in ListingStatus}


def public_listings():
    """THE queryset of publicly visible Listings (AD-5): Published, not restricted, owner an
    Active eligible owner. Lazy and internal to `listings`: it never crosses the app boundary
    (other apps get views). Shares its predicate with `policy.can_view`."""
    return PropertyListing.objects.filter(policy.public_q())


@dataclass(frozen=True)
class PublicImageView:
    """An image as the public sees it: no asset id, storage key or owner."""

    id: uuid.UUID
    position: int
    is_cover: bool
    content_type: str
    width: int
    height: int
    url: str


@dataclass(frozen=True)
class PublicListingView:
    """The public projection of a Listing (AD-5, AD-24, AD-26), built field by field from public
    registry fields only. It deliberately has no owner, status, restricted flag, provenance,
    revision or private-class field; the type, not review discipline, keeps them out."""

    id: uuid.UUID
    purpose: str
    category: str
    subtype: str
    title: str | None
    description: str | None
    fields: Mapping[str, object]  # the other registry fields of class `public`; null if unknown
    media: tuple[PublicImageView, ...]
    registry_version: int


# Fields shown at the top of the projection, not repeated in `fields`.
PROJECTED_APART = (*IDENTITY_FIELDS, "title", "description")


def public_fields(values: Mapping[str, object], definition=None) -> dict[str, object]:
    """The registry fields of class `public` from `values`. A field of any other class is never
    copied; a field the registry does not define raises (a new column must get a registry
    definition and a class before it can reach a public response). `definition` is a seam for
    the guard tests."""
    definition = definition or registry.field
    shown = {}
    for name, value in values.items():
        if name in PROJECTED_APART:
            continue
        found = definition(name)
        if found is None:
            raise RuntimeError(f"field {name!r} has no registry definition; it cannot be projected")
        if found.field_class == "public":
            shown[name] = value
    return shown


AVAILABLE = "available"
HEADER_AVAILABILITY = (AVAILABLE, "sold", "rented", "unavailable")


@dataclass(frozen=True)
class ListingHeader:
    """What a participant of a Conversation may see of its Listing now (FR-48): the title only
    while it is publicly visible, otherwise just whether it was sold or rented."""

    availability: str
    title: str | None = None


def listing_header(listing_id) -> ListingHeader:
    return listing_headers([listing_id])[listing_id]


def listing_headers(listing_ids) -> dict:
    """`ListingHeader` of each of these ids, keyed as given, in two queries whatever their
    number."""
    keys = {given: canonical_uuid(given) for given in listing_ids}
    wanted = [key for key in keys.values() if key is not None]
    titles = dict(public_listings().filter(id__in=wanted).values_list("id", "title"))
    hidden = {
        row_id: (status, restricted)
        for row_id, status, restricted in PropertyListing.objects.filter(
            id__in=[key for key in wanted if key not in titles]
        ).values_list("id", "status", "restricted")
    }
    closed = {ListingStatus.SOLD.value: "sold", ListingStatus.RENTED.value: "rented"}

    def header(key) -> ListingHeader:
        if key in titles:
            return ListingHeader(AVAILABLE, titles[key])
        status, restricted = hidden.get(key, (None, False))
        return ListingHeader("unavailable" if restricted else closed.get(status, "unavailable"))

    return {given: header(key) for given, key in keys.items()}


def public_view_of(
    row: PropertyListing,
    *,
    images: tuple[ImageView, ...] | None = None,
    amenities: Mapping[str, str] | None = None,
) -> PublicListingView:
    # Public callers pass only rows from `public_listings()`. The private address is never read.
    # `images` and `amenities` are the row's own, batch-loaded by a list (no query per row).
    view = view_of(row, public=True, with_private=False, images=images, amenities=amenities)
    return PublicListingView(
        id=row.id,
        purpose=row.purpose,
        category=row.category,
        subtype=row.subtype,
        title=row.title,
        description=row.description,
        fields=MappingProxyType(public_fields(view.fields)),
        media=tuple(
            PublicImageView(
                id=image.id,
                position=image.position,
                is_cover=image.is_cover,
                content_type=image.content_type,
                width=image.width,
                height=image.height,
                url=image.url,
            )
            for image in view.media
        ),
        registry_version=row.registry_version,
    )


def public_listing(viewer: Actor, listing_id) -> PublicListingView:
    """The public projection of one Listing, or `not_found` for every other case (unknown or
    malformed id, a Draft, restricted, owner Suspended or Deactivated, any viewer): existence is
    never confirmed. Public means the same for every viewer, so `viewer` changes nothing here;
    it is part of the signature so a later per-viewer extra (a favourite flag) has a place."""
    key = canonical_uuid(listing_id)
    if key is None:
        raise DomainError("not_found")
    row = public_listings().select_related(*PUBLIC_RELATED).filter(id=key).first()
    if row is None:
        raise DomainError("not_found")
    return public_view_of(row)


def public_owner_id(system: Actor, listing_id) -> uuid.UUID:
    """The owning account of a publicly visible Listing, for derived rows kept per account (a
    stored estimate). The system actor only; anything else is `not_found`."""
    key = canonical_uuid(listing_id)
    owner = None
    if system.kind is ActorKind.SYSTEM and key is not None:
        owner = public_listings().filter(id=key).values_list("owner_account_id", flat=True).first()
    if owner is None:
        raise DomainError("not_found")
    return owner


# --- public discovery (Story 3.1) ----------------------------------------------------------------

SORT_NEWEST = "newest"
SORT_PRICE_ASC = "price_asc"
SORT_PRICE_DESC = "price_desc"
SORT_RELEVANCE = "relevance"  # only with preference criteria
PUBLIC_SORTS = (SORT_NEWEST, SORT_PRICE_ASC, SORT_PRICE_DESC, SORT_RELEVANCE)

# the sort keys of each order, as (annotation or column, descending); the last one is unique
_ORDERS = {
    SORT_NEWEST: (("published_at", True), ("id", True)),
    SORT_PRICE_ASC: (("price_key", False), ("id", True)),
    SORT_PRICE_DESC: (("price_key", True), ("id", True)),
    SORT_RELEVANCE: (("score", True), ("published_at", True), ("id", True)),
}
# stands in for a missing price, so that it sorts last either way (publication requires one)
_NO_PRICE = {SORT_PRICE_ASC: Decimal("1e15"), SORT_PRICE_DESC: Decimal(-1)}
# the ORM path from a Listing to the table of a field (`listings.fields`)
_PATHS = {
    **{name: "" for name in IDENTITY_FIELDS},
    **{
        name: "" if table == LISTING else f"{table}__"
        for name, table in {**STORAGE, **DERIVED}.items()
    },
}


@dataclass(frozen=True)
class PlaceView:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class PublicEntry:
    """A public Listing as discovery shows it: the public projection plus the day it was first
    published, the names of its city and locality, and who advertises it."""

    listing: PublicListingView
    advertiser: identity.PublicAdvertiser
    published_on: date  # the Asia/Karachi calendar date
    city: PlaceView | None
    locality: PlaceView | None


@dataclass(frozen=True)
class PublicPage:
    items: tuple[PublicEntry, ...]
    next_cursor: str | None


def _published():
    """`public_listings()` with `published_at`: the time of the FIRST publication, so archiving
    and republishing never moves a Listing back to the top and the keyset position never changes.
    A row without one (only fixtures write such a row) counts from its creation. The expression
    is the one of `listings_public_newest_idx`."""
    return (
        public_listings()
        .select_related(*PUBLIC_RELATED)
        .annotate(published_at=Coalesce("first_published_at", "created_at"))
    )


def _entries(rows: list) -> tuple[PublicEntry, ...]:
    """Rows of `_published()` as entries, with a fixed number of queries whatever their count."""
    images = media_by_listing(r.id for r in rows)
    answered = amenities_by_listing(r.id for r in rows)
    places = [location for r in rows if (location := location_of(r)) is not None]
    cities = catalog.city_names(p.city for p in places)
    localities = catalog.locality_names(p.locality for p in places)
    advertisers = identity.public_advertisers(r.owner_account_id for r in rows)

    def place(names, key):
        return PlaceView(key, names[key]) if key in names else None

    entries = []
    for row in rows:
        location = location_of(row)
        entries.append(
            PublicEntry(
                listing=public_view_of(row, images=images[row.id], amenities=answered[row.id]),
                advertiser=advertisers[row.owner_account_id],
                published_on=row.published_at.astimezone(KARACHI).date(),
                city=place(cities, location.city) if location else None,
                locality=place(localities, location.locality) if location else None,
            )
        )
    return tuple(entries)


def _condition(criterion: filters.Criterion):
    """The query condition of one criterion. NULL (unanswered) never satisfies it."""
    if criterion.kind == filters.AMENITIES:
        return Q(
            Exists(
                ListingAmenity.objects.filter(
                    listing_id=OuterRef("pk"), amenity_code=criterion.values[0], state="present"
                )
            )
        )
    column = _PATHS[criterion.field] + criterion.field
    if criterion.kind == filters.CHOICE:
        return Q(**{f"{column}__in": criterion.values})
    if criterion.kind == filters.BOOLEAN:
        return Q(**{column: criterion.values[0]})
    bounds = {"gte": criterion.low, "lte": criterion.high}
    return Q(**{f"{column}__{op}": value for op, value in bounds.items() if value is not None})


def _matching(spec: filters.ListingFilter):
    """`_published()` narrowed to the required criteria: every public search and count starts
    here. A criterion on a field that is not filterable (a private one included) is refused."""
    allowed = {item.id for item in filters.filterable()}
    if not {c.field for c in (*spec.required, *spec.preferred)} <= allowed:
        raise ValueError("a criterion on a field that is not filterable")
    rows = _published()
    for criterion in spec.required:
        rows = rows.filter(_condition(criterion))
    return rows


def _ordered_by(rows, spec: filters.ListingFilter, sort: str):
    """`rows` with the annotation the sort needs."""
    if sort == SORT_RELEVANCE:
        points = [
            Case(When(_condition(c), then=Value(1)), default=Value(0), output_field=IntegerField())
            for c in spec.preferred
        ]
        return rows.annotate(score=sum(points[1:], points[0]))
    if sort in _NO_PRICE:
        money = DecimalField(max_digits=20, decimal_places=2)
        return rows.annotate(
            price_key=Coalesce(F("terms__asking_price"), Value(_NO_PRICE[sort]), output_field=money)
        )
    return rows


def _after(order, position) -> Q:
    """The rows after `position` in `order` (keyset pagination)."""
    condition, tied = Q(), {}
    for (name, descending), value in zip(order, position, strict=True):
        condition |= Q(**tied, **{f"{name}__{'lt' if descending else 'gt'}": value})
        tied[name] = value
    # implied by the above, but as one range on the first key an index scan can start there
    (first, descending), start = order[0], position[0]
    return Q(**{f"{first}__{'lte' if descending else 'gte'}": start}) & condition


def _search_tag(spec: filters.ListingFilter, sort: str) -> str:
    """Binds a cursor to the filter and the sort it was issued for."""
    return f"{sort}:{hashlib.sha256(repr(spec).encode()).hexdigest()[:16]}"


def _encode_position(tag: str, order, row) -> str:
    values = []
    for name, _ in order:
        value = getattr(row, name)
        values.append(value.isoformat() if isinstance(value, datetime) else str(value))
    raw = json.dumps({"s": tag, "k": values}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _sort_value(name: str, text: str):
    """One stored sort key back from its text, strictly."""
    if name == "id":
        value = canonical_uuid(text)
    elif name == "score":
        value = int(text) if len(text) <= 4 and text.isascii() and text.isdigit() else None
    elif name == "price_key":
        value = Decimal(text)
        if not value.is_finite() or abs(value) > _NO_PRICE[SORT_PRICE_ASC]:
            value = None
    else:
        value = datetime.fromisoformat(text)
        value = value.astimezone(UTC) if value.tzinfo else None
        if value is not None and not _MIN_YEAR <= value.year <= _MAX_YEAR:
            value = None
    if value is None:
        raise InvalidCursor
    return value


def _decode_position(cursor: str, tag: str, order) -> list:
    if not isinstance(cursor, str) or not _SEARCH_CURSOR.fullmatch(cursor):
        raise InvalidCursor
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(data, dict) or set(data) != {"s", "k"} or data["s"] != tag:
            raise InvalidCursor
        keys = data["k"]
        if not isinstance(keys, list) or len(keys) != len(order):
            raise InvalidCursor
        if not all(isinstance(key, str) for key in keys):
            raise InvalidCursor
        return [_sort_value(name, key) for (name, _), key in zip(order, keys, strict=True)]
    except (binascii.Error, ValueError, TypeError, ArithmeticError, RecursionError):
        raise InvalidCursor from None


def search_public(
    actor: Actor,
    spec: filters.ListingFilter,
    sort: str = SORT_NEWEST,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    owner_account_id: uuid.UUID | None = None,
) -> PublicPage:
    """The public Listings that satisfy the required criteria of `spec`, in the order of `sort`,
    keyset-paginated. Public means the same for every viewer, so `actor` changes nothing.
    `newest` is the first publication, latest first; `price_*` compares `asking_price` as stored;
    `relevance` is the number of preference criteria satisfied, then newest. The id breaks every
    tie. `sort` must be in `PUBLIC_SORTS`, and `relevance` needs a preference (ValueError
    otherwise); a bad `cursor`, or one of another filter or sort, raises `InvalidCursor`.
    `owner_account_id` narrows the same query to one advertiser (the Agency page, Story 3.8)."""
    if sort not in PUBLIC_SORTS or (sort == SORT_RELEVANCE and not spec.preferred):
        raise ValueError(f"unknown sort {sort!r}")
    order, tag = _ORDERS[sort], _search_tag(spec, sort)
    rows = _ordered_by(_matching(spec), spec, sort)
    if owner_account_id is not None:
        rows = rows.filter(owner_account_id=owner_account_id)
    if cursor is not None:
        rows = rows.filter(_after(order, _decode_position(cursor, tag, order)))
    size = max(1, min(int(limit), MAX_LIMIT))
    ordering = [f"-{name}" if descending else name for name, descending in order]
    page = list(rows.order_by(*ordering)[: size + 1])
    more = len(page) > size
    page = page[:size]
    next_cursor = _encode_position(tag, order, page[-1]) if more else None
    return PublicPage(_entries(page), next_cursor)


def public_detail(viewer: Actor, listing_id) -> PublicEntry:
    """One public Listing as discovery shows it; the same `not_found` rule as `public_listing`."""
    key = canonical_uuid(listing_id)
    row = _published().filter(id=key).first() if key is not None else None
    if row is None:
        raise DomainError("not_found")
    return _entries([row])[0]


# --- favourites (Story 3.7) -----------------------------------------------------------------------

# Who keeps favourites, expressed with the capabilities that cover exactly those account states:
# `view_drafts` is Active, Email-unverified and Suspended Individuals and Agencies (view and
# remove); `edit_drafts` the first two (save). Administrators and Deactivated accounts hold neither.
FAVOURITES_VIEW = Capability.VIEW_DRAFTS
FAVOURITES_SAVE = Capability.EDIT_DRAFTS


@dataclass(frozen=True)
class FavouriteItem:
    listing_id: uuid.UUID
    saved_at: datetime
    entry: PublicEntry | None  # None: the Listing is no longer public, so no details at all


@dataclass(frozen=True)
class FavouritePage:
    items: tuple[FavouriteItem, ...]
    next_cursor: str | None


def require_favourites(actor: Actor, capability: Capability = FAVOURITES_VIEW) -> None:
    """A Visitor is `unauthenticated`; an account that may not do this is `forbidden`."""
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, capability):
        raise DomainError("forbidden")


def favourite_ids(actor: Actor) -> list[uuid.UUID]:
    """The Listing ids the caller has saved (at most the cap), for the Save buttons."""
    require_favourites(actor)
    return list(
        SavedListing.objects.filter(account_id=actor.account_id)
        .order_by("-created_at", "-listing_id")
        .values_list("listing_id", flat=True)
    )


def list_favourites(
    actor: Actor, cursor: str | None = None, limit: int = DEFAULT_LIMIT
) -> FavouritePage:
    """The caller's own favourites, latest saved first, keyset-paginated. Details come only from
    `public_listings()`, with a fixed number of queries whatever the page size; a saved Listing
    that is not public now has `entry` None. A bad cursor raises `InvalidCursor`."""
    require_favourites(actor)
    rows = SavedListing.objects.filter(account_id=actor.account_id)
    if cursor is not None:
        saved_at, listing_id = decode_cursor(cursor, "favourites")
        rows = rows.filter(
            Q(created_at__lt=saved_at) | Q(created_at=saved_at, listing_id__lt=listing_id)
        )
    size = max(1, min(int(limit), MAX_LIMIT))
    page = list(
        rows.order_by("-created_at", "-listing_id").values_list("listing_id", "created_at")[
            : size + 1
        ]
    )
    more = len(page) > size
    page = page[:size]
    visible = list(_published().filter(id__in=[listing_id for listing_id, _ in page]))
    entries = {entry.listing.id: entry for entry in _entries(visible)}
    next_cursor = encode_cursor(page[-1][1], page[-1][0], "favourites") if more else None
    return FavouritePage(
        tuple(
            FavouriteItem(listing_id, saved_at, entries.get(listing_id))
            for listing_id, saved_at in page
        ),
        next_cursor,
    )


def preview_of(actor: Actor, listing_id) -> PublicListingView:
    """What the public will see of the owner's own Listing (Story 2.13), built by the SAME
    projection function as the public read (`public_view_of`: no second field list), minus the
    `is_public` gate, because the viewer is the owner. Allowed for the owner's own Listing in
    every status (Story 2.17; `policy.is_owner`: Active, Email-unverified and Suspended owners;
    not a Deactivated one). Everything else (another account, an administrator, a Visitor, an
    unknown or malformed id) is `not_found`, so existence is never confirmed.
    The private address is not read (`public_view_of` never joins it)."""
    key = canonical_uuid(listing_id)
    if key is None:
        raise DomainError("not_found")
    row = PropertyListing.objects.select_related(*PUBLIC_RELATED).filter(id=key).first()
    if row is None or not policy.is_owner(actor, row):
        raise DomainError("not_found")
    return public_view_of(row)


def image_for_serving(
    actor: Actor, listing_id, image_id, *, with_bytes: bool = True
) -> ServedImage:
    """THE seam of the media endpoint (AD-15, AD-22): the stored bytes of one image the actor may
    see, or `not_found` for everything else (the same answer for unknown or malformed ids,
    another Listing's image, a lost file and a Listing the actor may not view).

    The decision is the policy's (Story 2.12): `is_public`, else `is_owner`, the two halves of
    `can_view`, else `admin_may_inspect` (Story 9.3: an administrator, non-Draft Listings). An
    image of a public Listing goes to anyone, a Visitor included; an image of any other Listing
    goes only to its owner or a reviewing administrator. `ServedImage.public` says whether the
    Listing is public right now, so the endpoint may cache the answer.

    `with_bytes=False` (for `HEAD`) decides access the same way but reads no file: `data` is empty
    and `size` comes from the asset row.
    """
    listing_key, image_key = canonical_uuid(listing_id), canonical_uuid(image_id)
    if listing_key is None or image_key is None:
        raise DomainError("not_found")
    row = PropertyListing.objects.filter(id=listing_key).first()
    if row is None:
        raise DomainError("not_found")
    public = policy.is_public(row)  # once: it decides access and the cache header
    if not (public or policy.is_owner(actor, row) or policy.admin_may_inspect(actor, row)):
        raise DomainError("not_found")
    link = ListingImage.objects.filter(id=image_key, listing_id=listing_key).first()
    if link is None:
        raise DomainError("not_found")
    if not with_bytes:
        asset = media.assets_by_id([link.asset_id]).get(link.asset_id)
        if asset is None:
            raise DomainError("not_found")
        return ServedImage(
            content_type=asset.content_type, data=b"", size=asset.bytes, public=public
        )
    stored = media.read_asset(link.asset_id)
    if stored is None:
        raise DomainError("not_found")
    asset, data = stored
    return ServedImage(content_type=asset.content_type, data=data, size=len(data), public=public)


# --- draft retention candidates (Story 2.13); `listings.retention` acts on them ------------------

IDLE_DAYS = 180  # a Draft with no activity this long gets a notice
GRACE_DAYS = 30  # then it is deleted this much later unless the owner opened or edited it
RETENTION_BATCH = 200  # Drafts per keyset page
RETENTION_MAX_PAGES = 50  # pages per job run (so at most 10,000 Drafts); the next run does the rest
LATE_AFTER_DAYS = 2  # a deletion date this far past is not acted on (suspension or an outage)
REGRACE_DAYS = 7  # the fresh grace period then given
DRAFT_EXPIRING = "listing.draft_expiring"


def _activity():
    """The activity time of a Draft: the latest of its edit, open and creation times (SQL
    GREATEST ignores a NULL `last_opened_at`)."""
    return Greatest("updated_at", "last_opened_at", "created_at")


def activity_of(row: PropertyListing) -> datetime:
    """The same activity time for one row, as the services re-check it under the Listing lock."""
    return max(t for t in (row.updated_at, row.last_opened_at, row.created_at) if t is not None)


def idle_draft_ids(
    now: datetime, after: uuid.UUID | None = None, limit: int | None = None
) -> list[uuid.UUID]:
    """One keyset page (by id, after `after`) of Drafts to notice: no activity for 180 days
    (exactly 180 counts), no notice yet, owner neither Suspended nor Deactivated."""
    rows = (
        PropertyListing.objects.filter(
            status=ListingStatus.DRAFT.value, expiry_notice_sent_at__isnull=True
        )
        .annotate(activity=_activity())
        .filter(activity__lte=now - timedelta(days=IDLE_DAYS))
        .exclude(owner_account_id__in=identity.suspended_or_deactivated_account_ids())
        .order_by("id")
    )
    if after is not None:
        rows = rows.filter(id__gt=after)
    return list(rows.values_list("id", flat=True)[: limit or RETENTION_BATCH])


def due_draft_ids(
    now: datetime, after: uuid.UUID | None = None, limit: int | None = None
) -> list[uuid.UUID]:
    """One keyset page (by id, after `after`) of Drafts whose deletion date has come
    (`delete_after <= now`), owner neither Suspended nor Deactivated. The service decides, under
    the lock, whether each is deleted, cleared or given a fresh grace period."""
    rows = (
        PropertyListing.objects.filter(
            status=ListingStatus.DRAFT.value, delete_after__isnull=False, delete_after__lte=now
        )
        .exclude(owner_account_id__in=identity.suspended_or_deactivated_account_ids())
        .order_by("id")
    )
    if after is not None:
        rows = rows.filter(id__gt=after)
    return list(rows.values_list("id", flat=True)[: limit or RETENTION_BATCH])


def deletable_now(ids, now: datetime) -> int:
    """How many of these due Drafts the real run would delete (the dry run's count): activity not
    later than the notice, deletion date not more than 2 days past, and the notice really stored."""
    rows = PropertyListing.objects.filter(id__in=list(ids), expiry_notice_sent_at__isnull=False)
    return sum(1 for row in rows if deletable(row, now))


def deletable(row: PropertyListing, now: datetime) -> bool:
    """The three conditions under which a due Draft is deleted rather than cleared or re-noticed
    (the expiry service applies the same rules in its own order, with the reasons)."""
    return (
        row.delete_after is not None
        and row.expiry_notice_sent_at is not None
        and activity_of(row) <= row.expiry_notice_sent_at
        and now - row.delete_after <= timedelta(days=LATE_AFTER_DAYS)
        and notice_stored(row)
    )


def notice_stored(row: PropertyListing) -> bool:
    """Whether a `listing.draft_expiring` notification for this Listing is on the owner's list."""
    return notifications.has_with_payload(
        row.owner_account_id, DRAFT_EXPIRING, "listing_id", str(row.id)
    )


def titles_owned_by(owner_account_id) -> dict[str, tuple[uuid.UUID, str]]:
    """title -> (Listing id, status) of every Listing of one owner that has a title. Sample-data
    tooling only: it finds what a sample run already made."""
    rows = PropertyListing.objects.filter(
        owner_account_id=owner_account_id, title__isnull=False
    ).values_list("title", "id", "status")
    return {title: (listing_id, status) for title, listing_id, status in rows}


def existing_listing_ids(actor: Actor, listing_ids) -> set[uuid.UUID]:
    """Which of these Listing ids still exist, for retention sweeps (system actor only)."""
    if actor.kind is not ActorKind.SYSTEM:
        raise DomainError("forbidden")
    found = PropertyListing.objects.filter(id__in=list(listing_ids))
    return set(found.values_list("id", flat=True))


def listing_ids_owned_by(owner_account_ids) -> list[uuid.UUID]:
    """The ids of every Listing owned by any of the accounts, oldest first."""
    return list(
        PropertyListing.objects.filter(owner_account_id__in=list(owner_account_ids))
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )


def known_private_addresses(owner_account_id: uuid.UUID) -> KnownValues:
    """The owner's Private addresses, only for AI redaction (`kernel.ai.known_values`)."""
    rows = ListingPrivateAddress.objects.filter(listing__owner_account_id=owner_account_id)
    addresses = []
    for street, house_number in rows.values_list("street", "house_number"):
        addresses += [value for value in (street, house_number) if value]
        if street and house_number:
            addresses.append(f"{house_number} {street}")
    return KnownValues(addresses=tuple(addresses))


# --- review flags (Story 9.6) --------------------------------------------------------------------


@dataclass(frozen=True)
class DuplicateCandidate:
    """The facts of a Published Listing that the duplicate rule compares."""

    id: uuid.UUID
    owner_account_id: uuid.UUID
    purpose: str
    category: str
    locality: uuid.UUID
    area_sqft: Decimal
    price: Decimal | None
    title: str | None


def duplicate_candidates(actor: Actor, same_owner_as=None) -> list[DuplicateCandidate]:
    """Published Listings with a locality and an area, oldest first (system actor only); with
    `same_owner_as`, only those of that Listing's owner."""
    if actor.kind is not ActorKind.SYSTEM:
        raise DomainError("forbidden")
    rows = PropertyListing.objects.filter(
        status=ListingStatus.PUBLISHED.value,
        location__locality__isnull=False,
        area__area_sqft__isnull=False,
    )
    if same_owner_as is not None:
        owner = PropertyListing.objects.filter(id=same_owner_as).values("owner_account_id")
        rows = rows.filter(owner_account_id__in=owner)
    values = rows.order_by("created_at", "id").values_list(
        "id",
        "owner_account_id",
        "purpose",
        "category",
        "location__locality",
        "area__area_sqft",
        "terms__asking_price",
        "title",
    )
    return [DuplicateCandidate(*row) for row in values]


def admin_listing_rows(actor: Actor, listing_ids) -> dict[uuid.UUID, "AdminListingRow"]:
    """The review-list row of each of these Listings that exists; a Draft without its title."""
    require_admin(actor)
    rows = PropertyListing.objects.filter(id__in=list(listing_ids))
    return {r.id: _admin_row(r, with_title=r.status != ListingStatus.DRAFT.value) for r in rows}


# --- moderation review (Story 9.3): administrators only ------------------------------------------


@dataclass(frozen=True)
class AdminListingRow:
    """A Listing in the review list. A Draft's title is not shown: its content is read only with
    a recorded reason."""

    id: uuid.UUID
    owner_account_id: uuid.UUID
    status: str
    restricted: bool
    purpose: str
    category: str
    subtype: str
    title: str | None
    created_at: datetime


@dataclass(frozen=True)
class AdminListingPage:
    items: tuple[AdminListingRow, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class AdminListingDetail:
    """What a reviewing administrator sees: the public projection of the content (never the
    Private address), the moderation state and the actions open now."""

    row: AdminListingRow
    listing: PublicListingView
    city: str | None
    locality: str | None
    reason: str | None  # the moderation reason the owner currently sees
    actions: tuple[str, ...]
    first_published_at: datetime | None


def _admin_row(row: PropertyListing, *, with_title: bool) -> AdminListingRow:
    return AdminListingRow(
        id=row.id,
        owner_account_id=row.owner_account_id,
        status=row.status,
        restricted=row.restricted,
        purpose=row.purpose,
        category=row.category,
        subtype=row.subtype,
        title=row.title if with_title else None,
        created_at=row.created_at,
    )


def admin_listing_page(
    actor: Actor,
    *,
    owner_id: uuid.UUID | None = None,
    category: str | None = None,
    status: str | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
    cursor: str | None = None,
) -> AdminListingPage:
    """Listings newest first, 20 per page. Without a `status` filter Drafts are left out;
    `status="draft"` lists them without content. Dates are Karachi calendar days, inclusive."""
    require_admin(actor)
    rows = PropertyListing.objects.all()
    if status is None:
        rows = rows.exclude(status=ListingStatus.DRAFT.value)
    else:
        rows = rows.filter(status=ListingStatus(status).value)
    if owner_id is not None:
        rows = rows.filter(owner_account_id=owner_id)
    if category is not None:
        rows = rows.filter(category=category)
    if created_from is not None:
        rows = rows.filter(created_at__gte=datetime.combine(created_from, time.min, KARACHI))
    if created_to is not None:
        end = datetime.combine(created_to, time.min, KARACHI) + timedelta(days=1)
        rows = rows.filter(created_at__lt=end)
    tag = "|".join(str(v or "") for v in (owner_id, category, status, created_from, created_to))
    tag = "admin:" + hashlib.sha256(tag.encode()).hexdigest()[:16]
    if cursor is not None:
        created_at, last_id = decode_cursor(cursor, tag)
        rows = rows.filter(Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=last_id))
    page = list(rows.order_by("-created_at", "-id")[: DEFAULT_LIMIT + 1])
    more = len(page) > DEFAULT_LIMIT
    page = page[:DEFAULT_LIMIT]
    return AdminListingPage(
        tuple(_admin_row(r, with_title=r.status != ListingStatus.DRAFT.value) for r in page),
        encode_cursor(page[-1].created_at, page[-1].id, tag) if more else None,
    )


def _admin_row_of(listing_id) -> PropertyListing:
    key = canonical_uuid(listing_id)
    row = (
        PropertyListing.objects.select_related(*PUBLIC_RELATED).filter(id=key).first()
        if key is not None
        else None
    )
    if row is None:
        raise DomainError("not_found")
    return row


def _admin_detail(row: PropertyListing) -> AdminListingDetail:
    location = location_of(row)
    city = catalog.city_names([location.city]) if location else {}
    locality = catalog.locality_names([location.locality]) if location else {}
    return AdminListingDetail(
        row=_admin_row(row, with_title=True),
        listing=public_view_of(row),
        city=city.get(location.city) if location else None,
        locality=locality.get(location.locality) if location else None,
        reason=lifecycle.shown_reason(row),
        actions=lifecycle.moderation_actions(row),
        first_published_at=row.first_published_at,
    )


def admin_listing(actor: Actor, listing_id) -> AdminListingDetail:
    """One non-Draft Listing. A Draft is `not_found` here: see `admin_read_draft_listing`."""
    require_admin(actor)
    row = _admin_row_of(listing_id)
    if row.status == ListingStatus.DRAFT.value:
        raise DomainError("not_found")
    return _admin_detail(row)


def draft_id_for_read(actor: Actor, listing_id) -> uuid.UUID:
    """The id of an existing Draft, else `not_found`; reads nothing else."""
    require_admin(actor)
    key = canonical_uuid(listing_id)
    drafts = PropertyListing.objects.filter(id=key, status=ListingStatus.DRAFT.value)
    if key is None or not drafts.exists():
        raise DomainError("not_found")
    return key


def admin_read_draft_listing(actor: Actor, reason: str, listing_id: uuid.UUID):
    """A reasoned private read (`docs/audit.md`) of one Draft's content. Its images are listed
    but not served to administrators, and the Private address is not read."""
    record_private_read(actor, reason, ("listing_draft", listing_id))
    row = _admin_row_of(listing_id)
    if row.status != ListingStatus.DRAFT.value:
        raise DomainError("not_found")
    return _admin_detail(row)
