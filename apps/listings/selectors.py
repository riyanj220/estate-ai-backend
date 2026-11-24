"""Read path for Listings. Returns plain immutable views, never model instances (AD-3).

The owner-facing reads (`get_owned_listing`, `list_owned`) are owner-only: any other account, a
Visitor and an administrator get `not_found`, so the existence of a Listing is never confirmed.
Public reads start from `public_listings()` / `public_listing` / `listings.policy.can_view`, the
only implementations of visibility (AD-5, `docs/visibility.md`).
"""

import base64
import binascii
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q

from apps.catalog import registry
from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.kernel import media
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.listings import policy
from apps.listings.area import basis_text
from apps.listings.fields import (
    ADDRESS_FIELDS,
    AMENITIES_FIELD,
    AREA_FIELDS,
    COMMERCIAL_FIELDS,
    DERIVED_FIELDS,
    IDENTITY_FIELDS,
    LISTING_FIELDS,
    LOCATION_FIELDS,
    PLOT_FIELDS,
    RESIDENTIAL_FIELDS,
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
)

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
_MIN_YEAR, _MAX_YEAR = 2000, 9000
_CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
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
    """A cursor that is malformed or was not issued by `list_owned`."""


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
    )


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
_PROJECTED_APART = (*IDENTITY_FIELDS, "title", "description")


def public_fields(values: Mapping[str, object], definition=None) -> dict[str, object]:
    """The registry fields of class `public` from `values`. A field of any other class is never
    copied; a field the registry does not define raises (a new column must get a registry
    definition and a class before it can reach a public response). `definition` is a seam for
    the guard tests."""
    definition = definition or registry.field
    shown = {}
    for name, value in values.items():
        if name in _PROJECTED_APART:
            continue
        found = definition(name)
        if found is None:
            raise RuntimeError(f"field {name!r} has no registry definition; it cannot be projected")
        if found.field_class == "public":
            shown[name] = value
    return shown


def public_view_of(row: PropertyListing) -> PublicListingView:
    # Callers pass only rows from `public_listings()`. The private address is never read.
    view = view_of(row, public=True, with_private=False)
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


def image_for_serving(
    actor: Actor, listing_id, image_id, *, with_bytes: bool = True
) -> ServedImage:
    """THE seam of the media endpoint (AD-15, AD-22): the stored bytes of one image the actor may
    see, or `not_found` for everything else (the same answer for unknown or malformed ids,
    another Listing's image, a lost file and a Listing the actor may not view).

    The decision is the policy's (Story 2.12): `is_public`, else `is_owner`, the two halves of
    `can_view`. An image of a public Listing goes to anyone, a Visitor included; an image of any
    other Listing goes only to its owner. `ServedImage.public` says whether the Listing is public
    right now, so the endpoint may cache the answer.

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
    if not (public or policy.is_owner(actor, row)):
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
