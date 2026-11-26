# Visibility policy and the public projection (Story 2.12)

One answer to "may this viewer see this Listing" (AD-5), and one public shape that can never carry the private address or
phone. The public browse and detail routes (Story 3.1, below) are built only from it.

## The rule

A Listing is **public** iff all three hold:

1. `status == published`,
2. `restricted` is false,
3. its owner is an Active eligible owner (`identity.selectors.is_active_owner`: an Active Individual or Agency account).

`Email-unverified` is a separate account state from `Active` (`identity.state.derive_state`), so an Email-unverified
owner's Listings are **not** public (they still see their own). Suspended and Deactivated owners' Listings are not public
until the account is Active again.

`published` is set only by `listings.publication` (`publish` for a Draft, Story 2.15, and `republish` for an Archived Listing, Story 2.18, `POST /api/v1/listings/{id}/republish`; a republished Listing is public again at once under this same rule when its owner is Active and it is not restricted, `tests/listings/test_republication.py`). `publish` (Story 2.15, `POST /api/v1/listings/{id}/publish`): a
published Listing becomes public at once under this rule when its owner is Active (`tests/listings/test_publication.py`
checks `public_listing` for an Active owner and `not_found` once the owner is Email-unverified). Moderation (a later
epic) sets `restricted`; until then tests and fixtures write it through the model (see
`tests/listings/support.py: set_listing_visibility`, `set_account_state`).

## The two implementations (nothing else decides)

- `listings.selectors.public_listings()`: a lazy `PropertyListing` queryset of public rows. Internal to `listings`: it
  never crosses the app boundary (other apps get views, AD-3).
- `listings.policy.can_view(viewer, listing) -> bool` (= `is_public or is_owner`): public Listings are visible to every
  viewer; any other Listing only to its owning account (a signed-in, non-administrator account actor holding
  `view_drafts`: Suspended and Email-unverified owners yes, Deactivated no). The owner branch is explicit over the known
  statuses (`policy.OWNER_VISIBLE_STATUSES`: since Story 2.17 all six, draft, published, archived, sold, rented and
  removed): a status added later is invisible to everyone, the owner included, until its story adds it there
  deliberately. Archived, sold, rented, removed and draft Listings are never public (`public_q()` is unchanged:
  published, not restricted, Active owner); the owner reads them, restricted and removed ones with their
  `restriction_reason`, which no public shape carries. A Visitor, another account and an administrator get `False` for a
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

## Public discovery (Story 3.1)

`selectors.search_public(actor, spec, sort, cursor, limit)` and `selectors.public_detail(viewer, id)` start from
`public_listings()` and build each row with the same `public_view_of`. `spec` is a `catalog.filters.ListingFilter`
(see Filters below).
They add the date of the first publication (Asia/Karachi) and the names of the city and locality. A list uses a fixed
number of queries whatever its size.

| Route | Answer |
| --- | --- |
| `GET /api/v1/public/listings?<filters>&sort=&limit=20&cursor=` | `{results: [card], next}` |
| `GET /api/v1/public/filters` | `{registry_version, sorts, preference_prefix, filters}` |
| `GET /api/v1/public/listings/{id}` | the public projection plus `city`, `locality`, `published_on` |

A card is `id`, `purpose`, `category`, `subtype`, `title`, `fields` (price, area, bedrooms, bathrooms), `city`,
`locality` (`{id, name}` or null), `cover_url`, `published_on`. Neither route authenticates anyone: the answer is the
same for every caller. A `200` is `Cache-Control: public, max-age=60` with no cookie; every error is `no-store`. A
Listing that is not public, an unknown and a malformed id are the same `404`. A bad `limit` (1 to 50), `cursor`,
`sort` or filter is `400`.

`newest` orders by the FIRST publication event, then id, so archiving and republishing cannot move a Listing back to
the top. The routes have no application rate limit.

## Filters (Story 3.3)

`catalog.filters` generates the filters from the registry: `purpose`, `category`, `subtype` and every public
`searchable` field of a structured type. Free text (`title`, `description`) has no filter, and `area_value` and
`area_unit` are left out in favour of `area_sqft`. A newly searchable field needs no code. `parse(params)` returns the
abstract `ListingFilter` (required and preferred criteria); `search_public` is the only code that turns it into a query.

| Kind | Fields | Parameters |
| --- | --- | --- |
| choice (any of) | `purpose`, `category`, `subtype`, `price_basis`, `rental_period`, `furnishing`, `city`, `locality` | `<id>=a,b` |
| range (inclusive) | `asking_price`, `area_sqft`, `bedrooms`, `bathrooms`, `parking_spaces` | `<id>_min`, `<id>_max` |
| amenities (all of, state `present`) | `amenities`, `nearby` codes included (seller-declared) | `amenities=a,b` |
| boolean | none yet | `<id>=true` or `false` |

- Required criteria AND together. A Listing that has not answered a field never matches a criterion on it, so a marla
  or kanal area without a basis is left out of any area filter.
- `prefer_<parameter>` never excludes. Relevance is the number of preferences met (each amenity code is one; the two
  bounds of a range are one).
- `sort`: `newest`, `price_asc`, `price_desc`, `relevance` (needs a preference, and is then the default). The ties of
  `relevance` fall to `newest`, and the id breaks every tie.
- Price compares `asking_price` as stored: a `per_unit` price is not multiplied out and rental periods are not
  converted. `purpose`, `price_basis` and `rental_period` keep unlike prices apart.
- The cursor carries the sort keys and a hash of the filter and sort: used with another, it is `400`.
- `400 validation_error` names each unknown, repeated or bad parameter and `<id>_max` for an inverted range. An amenity
  code must be active; a well-formed city or locality id that matches nothing gives an empty list.

## How later stories must use it

- A list, search, count, favourite, chat or detail for anyone other than the owner starts from `public_listings()` or
  `can_view` and adds only its own filters on top. Never filter on `status == published` or `restricted` elsewhere.
- Return `PublicListingView` (or a new frozen projection built the same way) from public reads; never `ListingView`, a
  model instance or `__dict__`.
- Answer a refusal with `not_found`, never `forbidden`.
- A new field class or status goes through `visibility_of`, the predicate in `policy.public_q()` and the matrix test.

## Privacy gates (Story 3.2)

A new public or other-account-facing route is checked automatically for private fields in its schema and private values
in its responses and logs (`tests/privacy`, run by `pytest -m guard`; see `docs/testing.md`).

## Caching and takedown delay

A public image is sent with `Cache-Control: public, max-age=60`, so after moderation restricts a Listing, or its owner is
suspended, a browser or shared cache may keep serving its images for **up to 60 seconds** (the same holds for the public browse and detail answers). The server itself refuses
them on the very next request. There is no code to purge caches; this delay is accepted (the server itself
always reads the current rows).

## OpenAPI

`openapi.yaml` changes with this story in these places only: the media endpoint's summary and description,
`published` in `ListingStatusEnum` and in the `status` filter of `GET /api/v1/me/listings`, and `public` in
`VisibilityEnum` (the Listing resource's `visibility` still references `VisibilityEnum`; `FieldClassEnum` is
unchanged). `private` and `public` are also the registry field classes, so the serializer gives `visibility`
labelled choices (`(value, NAME)`) and `ENUM_NAME_OVERRIDES` names it; otherwise drf-spectacular merges the two enums
and the frontend type name is lost (`tests/api/test_openapi.py` pins it).
