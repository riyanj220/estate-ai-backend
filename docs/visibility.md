# Visibility policy and the public projection (Story 2.12)

One answer to "may this viewer see this Listing" (AD-5), and one public shape that can never carry the private address or
phone. Backend only: there is no public listing route yet (discovery and detail come in later stories).

## The rule

A Listing is **public** iff all three hold:

1. `status == published`,
2. `restricted` is false,
3. its owner is an Active eligible owner (`identity.selectors.is_active_owner`: an Active Individual or Agency account).

`Email-unverified` is a separate account state from `Active` (`identity.state.derive_state`), so an Email-unverified
owner's Listings are **not** public (they still see their own). Suspended and Deactivated owners' Listings are not public
until the account is Active again.

`restricted` and `published` are not set by any service or route yet. Moderation (a later epic) sets `restricted`, and
the publication story (2.15) sets `published`; until then tests and fixtures write them through the model (see
`tests/listings/support.py: set_listing_visibility`, `set_account_state`).

## The two implementations (nothing else decides)

- `listings.selectors.public_listings()`: a lazy `PropertyListing` queryset of public rows. Internal to `listings`: it
  never crosses the app boundary (other apps get views, AD-3).
- `listings.policy.can_view(viewer, listing) -> bool` (= `is_public or is_owner`): public Listings are visible to every
  viewer; any other Listing only to its owning account (a signed-in, non-administrator account actor holding
  `view_drafts`: Suspended and Email-unverified owners yes, Deactivated no). The owner branch is explicit over the known
  statuses (`policy.OWNER_VISIBLE_STATUSES`: draft, published): a status added later (archived, removed, ...) is
  invisible to everyone, the owner included, until its story adds it there deliberately. A Visitor, another account and an administrator get `False` for a
  non-public Listing.

Both are built from `policy.public_q()` (the one predicate; the Active-owner part is the subquery
`identity.selectors.active_owner_account_ids()`, which a test keeps equal to `is_active_owner` for every account type
and state). `policy.is_public(listing)` re-reads the row through the same predicate, so it never trusts a stale instance.
`tests/listings/test_visibility.py` asserts for every matrix cell (status x restricted x owner state x viewer) that
`can_view`, `public_listings()` membership, `public_listing` and image serving agree, and
`tests/architecture/test_visibility_single_source.py` fails if any other module names the published status or the
`restricted` flag.

The owner-facing `visibility` field of the Listing resource is derived from the same predicate (`view_of` calls
`policy.is_public`; `list_owned` uses one batched query, `policy.public_ids`): it says `public` only while the Listing
really is public, and `private` for a Draft, a restricted Listing or one whose owner is not Active. The wire shape and
values are unchanged. `lifecycle.visibility_of(status, public=...)` is only the label.

`public_listings()` is backed by the index `listings_public_idx` on (`status`, `restricted`, `owner_account_id`).

## Not found, never forbidden

`selectors.public_listing(viewer, listing_id) -> PublicListingView` returns the projection for a public Listing and
raises `DomainError("not_found")` for everything else (unknown or malformed id, Draft, restricted, owner Suspended or
Deactivated, any viewer), so existence is never confirmed. The owner-facing `get_owned_listing` is unchanged.

## The public projection

`PublicListingView` (and `PublicImageView`) are frozen dataclasses built field by field in `listings.selectors`:
`id`, `purpose`, `category`, `subtype`, `title`, `description`, `fields` (the other registry fields whose class is
`public`; a private-class or unknown field is never copied, see `public_fields`), `media` (ordered; `id`, `position`,
`is_cover`, `content_type`, `width`, `height`, `url`) and `registry_version`. It has no owner id, status, restricted flag,
provenance, revision or history, asset id, storage key, and no private address or phone (Story 2.7: `street`, `house_number`, `latitude` and `longitude` are class `private`, live in their own table and are not even read on the public path; the guard test now bites on those real fields). The derived `area_sqft` is
class `public` in the registry, so it is projected (a test asserts it, and `asking_price`, `price_basis`, `area_value`,
`area_unit` by value). A field in the owner view with no registry definition makes `public_fields` raise instead of
being dropped silently, and a field the registry marks `private` is omitted (end-to-end test with a patched registry). It is a different type from
`ListingView` (the owner's wire shape) on purpose: the type, not review discipline, keeps owner-only data out of public
responses. `tests/listings/test_public_projection.py` walks the dataclass fields and serialised form, and proves the
private-class guard can fail by feeding it a registry that marks a field private.

## Preview by the owner (Story 2.13)

`selectors.preview_of(actor, listing_id)` returns the same `PublicListingView`, built by the same `public_view_of`, for the
owner's own Draft or Published Listing (`policy.is_owner`), without the `is_public` gate. It is the only place the owner
sees the public shape of a non-public Listing; everyone else keeps `not_found`. `GET /api/v1/listings/{id}/preview` adds
`estimate: null` and is `no-store`. See `docs/listings.md`.

## Media

`image_for_serving` asks `can_view`. See `docs/media.md`: a public Listing's images go to anyone with `Cache-Control:
public, max-age=60` and no cookie or `Vary: Cookie`; any other image goes only to the owner with `private, no-store`;
everyone else gets the identical `404`. An administrator may fetch images of a public Listing like any Visitor, but gets
404 for a non-public one. `HEAD` decides exactly like `GET`.

## How later stories must use it

- A list, search, count, favourite, chat or detail for anyone other than the owner starts from `public_listings()` or
  `can_view` and adds only its own filters on top. Never filter on `status == published` or `restricted` elsewhere.
- Return `PublicListingView` (or a new frozen projection built the same way) from public reads; never `ListingView`, a
  model instance or `__dict__`.
- Answer a refusal with `not_found`, never `forbidden`.
- A new field class or status goes through `visibility_of`, the predicate in `policy.public_q()` and the matrix test.

## Caching and takedown delay

A public image is sent with `Cache-Control: public, max-age=60`, so after moderation restricts a Listing, or its owner is
suspended, a browser or shared cache may keep serving its images for **up to 60 seconds**. The server itself refuses
them on the very next request. There is no code to purge caches; this delay is accepted and applies to images only
(`public_listing` and the lists always read the current rows).

## OpenAPI

`openapi.yaml` changes with this story in these places only: the media endpoint's summary and description,
`published` in `ListingStatusEnum` and in the `status` filter of `GET /api/v1/me/listings`, and `public` in
`VisibilityEnum` (the Listing resource's `visibility` still references `VisibilityEnum`; `FieldClassEnum` is
unchanged). `private` and `public` are also the registry field classes, so the serializer gives `visibility`
labelled choices (`(value, NAME)`) and `ENUM_NAME_OVERRIDES` names it; otherwise drf-spectacular merges the two enums
and the frontend type name is lost (`tests/api/test_openapi.py` pins it).
