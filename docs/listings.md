# Listings

`apps/listings` owns the canonical Listing. Story 2.1 adds starting a Draft; later stories add fields, editing,
media and publication (`_bmad-output/implementation-artifacts/epic-2-context.md`).

## Model (`listings.models`, written only by `listings.services`)

- `PropertyListing`: `id` (UUID), `owner_account_id` (a plain indexed UUID column, no foreign key into `identity`),
  `status` (`draft` now; the code-defined enum `listings.lifecycle.ListingStatus` grows later), `revision` (starts at 1),
  `registry_version`, the registry fields `purpose`, `category`, `subtype` as columns with CHECK constraints generated
  from the taxonomy (including that the Subtype belongs to the Category), `created_at`, `updated_at`. Index:
  (`owner_account_id`, `status`, `-created_at`).
- `ListingRevision`: `listing`, `revision`, `origin` (`manual`), `actor_account_id`, `changes` (the registry fields set at
  that revision), unique (`listing`, `revision`).

Models never leave the app: callers get frozen `ListingView` values. Other apps import only `listings.services`,
`listings.selectors` and `listings.lifecycle`.

## Creating a Draft: `services.create_draft(actor, purpose, category, subtype)`

In order: Visitor `unauthenticated`; the actor must be a signed-in user holding `edit_drafts` through
`identity.policy.can`, otherwise `forbidden` (Suspended, Deactivated, administrators and system actors never create);
the three values are validated against the registry, all failing fields at once (`required`, `invalid`,
`invalid_choice`, `subtype_not_in_category`; values never echoed) as `FieldsInvalid` (`validation_error`); then in one
transaction: `lock_account(owner)`, a re-check of the capability, the open-Draft cap (see `docs/rate-limits.md`; `429
throttled` without `Retry-After`), insert the listing with `revision = 1` and the current `registry_version`, insert
revision 1, write audit `listing.draft_created` (target the listing; metadata `purpose`, `category`, `subtype` only).
The owner is the actor's account, so an Agency's Drafts belong to the Agency and are shared by every session of it.
A client never supplies an owner, status or revision (unknown body fields are `unknown_field`).

## Reads

`selectors.get_owned_listing(actor, id)`: the owner only (`view_drafts`, so a Suspended owner may read); another
account, an unknown or malformed id, a Visitor, an administrator and a Deactivated account all get `not_found`, never
forbidden, so existence is never confirmed. `selectors.list_owned(actor, status, cursor, limit)`: own listings newest
first (`created_at`, then `id`), keyset-paginated with an opaque cursor (`InvalidCursor` on a bad one), 20 per page, up
to 50. A Visitor is `unauthenticated`; an account without `view_drafts` is `forbidden`.

## Route differences

The own-list route (`/api/v1/me/listings`) returns `403 forbidden` for administrators and Deactivated accounts, while
the detail route returns `404 not_found` for them (and for every non-owner). The list cursor is bound to its `status`
filter (a mismatch is `fields.cursor = invalid`); an empty `?cursor=` is page one. Ids must be canonical lowercase
hyphenated UUIDs. Indexes: (`owner_account_id`, `-created_at`, `-id`) and (`owner_account_id`, `status`, `-created_at`, `-id`).
The eligibility check is repeated on the locked account snapshot, so an account suspended or deactivated after the first
check is still refused.

## API

| Route | Notes |
|---|---|
| `POST /api/v1/listings` | body `{purpose, category, subtype}`; `201` with the Listing; CSRF; no-store |
| `GET /api/v1/listings/{id}` | owner only, `404` otherwise; no-store |
| `GET /api/v1/me/listings` | own listings, `status` filter, `page_size`, `cursor`; `{next, previous, results}` (`previous` is always null); bad cursor or status is `validation_error`; no-store |
| `GET /api/v1/registry` | public Field registry (`docs/registry.md`) |

## Wire shape (AD-24)

`{id, status, visibility, revision, registry_version, fields: {purpose, category, subtype}, media: [], amenities: []}`.
`visibility` is `private` for Drafts (`listings.lifecycle.Visibility`, extended by Story 2.12). `fields` is keyed by flat
registry ids and returns `null` for unknown. No owner or other internal column is returned. `tests/api/test_listings_api.py`
asserts the shape and the OpenAPI schema.

## Adding fields later

1. Add the field to `apps/catalog/registry/fields.yaml`, raise `version`, run `scripts/registry_lock.py` (`docs/registry.md`).
2. Add the storage (a column or a detail table) in `listings` with a migration, expose it in `selectors.view_of` as a flat
   registry id, and accept it through the revision-checked edit service (Story 2.2).
3. Serializers take enums from `catalog.registry`; never hand-copy values. New audit metadata is registered in
   `listings.apps` with enumerations only.
