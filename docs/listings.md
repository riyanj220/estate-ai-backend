# Listings

`apps/listings` owns the canonical Listing. Story 2.1 adds starting a Draft, Story 2.2 conflict-safe editing of title,
description and price; later stories add more fields, media and publication (`_bmad-output/implementation-artifacts/epic-2-context.md`).

## Model (`listings.models`, written only by `listings.services`)

- `PropertyListing`: `id` (UUID), `owner_account_id` (a plain indexed UUID column, no foreign key into `identity`),
  `status` (`draft` now; the code-defined enum `listings.lifecycle.ListingStatus` grows later), `revision` (starts at 1),
  `registry_version`, the registry fields `purpose`, `category`, `subtype` as columns with CHECK constraints generated
  from the taxonomy (including that the Subtype belongs to the Category), `title` and `description` (nullable, empty is
  `null`, length CHECKs from the registry), `provenance` (JSON, registry field id to `{"origin", "confirmed"}`, set
  fields only), `created_at`, `updated_at`. Index: (`owner_account_id`, `status`, `-created_at`).
- `ListingTerms`: one row per Listing (`listing` one-to-one), created with the Draft and lazily by `apply_updates` for
  older Drafts: `asking_price` NUMERIC(14,2) nullable (CHECK between the registry bounds), `price_basis` and
  `rental_period` nullable (CHECKs generated from the registry values), `updated_at`. Money is NUMERIC, never a float.
- `ListingRevision`: `listing`, `revision`, `origin` (`manual` or `ai`), `actor_account_id`, `changes` (the registry
  fields set at that revision and their new values as strings or null), unique (`listing`, `revision`). Edits are
  recorded by these rows; there is no separate audit action for them.

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

## Editing: `services.apply_updates(actor, listing_id, base_revision, updates, origin="manual")`

The only function that changes Listing fields after creation. It returns the new `ListingView`.

Refusals, in order: Visitor `unauthenticated`; a listing id that is not a canonical UUID of a Listing owned by the
actor's account `not_found` (another owner, an administrator, a system actor and an unknown id are indistinguishable);
no `edit_drafts` capability `forbidden` (Suspended, Deactivated; evaluated after the not-found rule); then the request
shape, `validation_error` with all failing fields at once and codes only: `base_revision` must be a positive integer
(`required`, `invalid`), `updates` a non-empty mapping (`required`, `not_a_dict`, `empty`), every key a registry field id
that this story makes editable (`title`, `description`, `asking_price`, `price_basis`, `rental_period`): other registry
ids such as `purpose` are `not_editable`, unknown ids (`owner_account_id`, `status`, `origin`, ...) are `unknown_field`
under `non_field_errors` so a client-chosen key is never echoed.

Then, in ONE transaction that locks only the listing row (`select_for_update`):

0. **Shape errors come first** (before the lock): unknown or non-editable ids and a bad `base_revision` or `fields`
   shape need no stored data, so they are reported as `400` even when the base revision is also stale.
1. **The stale check precedes every value validation.** A stored `revision` different from `base_revision` raises `StaleRevision` (a
   `DomainError` with code `stale_revision` that carries the current `ListingView` as `.current`); nothing is written and
   no value was validated yet. A client that is behind always learns about the conflict before anything else.
2. The status gate: a status outside `lifecycle.EDITABLE_STATUSES` is `forbidden`. The set is code-defined and holds only
   `draft` today; Story 2.15 (editing a Published Listing) widens it deliberately.
3. Every update is validated against the CURRENT registry (`listings.validation.evaluate`, all failures at once as
   `FieldsInvalid`): value rules per registry type, whether the field applies to this Listing (`not_applicable`) and the
   rules on the resulting state (below). A `null` clears a field.
4. An update that changes nothing (every value equals the stored one after normalisation) writes nothing, keeps the
   revision and returns the current view.
5. Otherwise: the fields are written, provenance is set for each changed field (removed for a cleared one),
   `registry_version` advances to the current version (so a Draft created under an older registry is revalidated and
   moved forward; a Listing in a non-editable status is never re-versioned or altered by a registry change),
   `revision` increases by exactly one, one `ListingRevision` is written, and last in the block
   `listing.model_inputs_changed` is emitted when a field flagged `valuation_input` changed. A failure after the write
   rolls everything back, the lazily created terms row, the revision row and the event included.

Saving never requires complete data. Retrying a PATCH that already succeeded carries the old `base_revision`, so it is a
409 (never a double write): there is no idempotency key, the client reloads from `current`. Edit volume is bounded only
by the global API limits for now; a per-account edit limit can be added if abuse appears.

Older registries: the revalidation of a Draft created under an older registry covers the SUBMITTED fields against the
current registry; stored values are not rechecked on edit (they are rechecked at publication, Story 2.15).

Blank or whitespace-only text clears a field (the same as `null`).

### Where fields are stored: `listings/fields.py`

`STORAGE` is the single map from editable registry field id to its table (`listing` column or `terms` column).
Validation, the edit service, the read views and the API serializers all derive their field sets from it, so adding an
editable field is one entry, its registry definition and a migration; `tests/listings/test_fields.py` fails, naming the
field, when the registry and the map disagree.

### Field rules (all from the registry, `docs/registry.md`)

- `title` (10 to 100 characters) and `description` (up to 5000): the raw input is bounded first (over 20 000 characters is
  `too_long`, no scanning), control and invisible characters are `invalid` (ZWNJ and ZWJ are fine inside a word, not at
  an end; tab, newline and carriage return are whitespace), the text is NFC-normalised, trimmed and its whitespace
  collapsed (a description keeps paragraph breaks as single newlines, at most one blank line in a row), then length
  (`too_short`, `too_long`), at least one letter or digit, and the shared contact-detail check
  `kernel.privacy.patterns.reject_contact_details` (`contains_contact_details`: phone numbers, email addresses and street
  addresses; see `docs/privacy-patterns.md`). LRM and RLM (U+200E, U+200F) are allowed, bidi overrides and isolates
  (U+202A to U+202E, U+2066 to U+2069) and other invisible characters are `invalid`. Blank text clears the field.
- `asking_price` (money, PKR): a decimal string, or an integer or `Decimal` from other callers; floats and booleans are
  never money. Greater than zero, at most 999 999 999 999.99 and at most two decimals (`invalid_price`; anything that is
  not a number is `invalid`). Stored as NUMERIC(14,2), serialised as a string with two decimals (`"1500000.00"`).
- `price_basis` (`total`, `per_unit`, `rental_period`): a sale allows `total` and `per_unit`, a rent only `rental_period`
  (`allowed_values_by_purpose` in the registry); anything else is `invalid_choice`.
- `rental_period` (`day`, `week`, `month`, `year`): applies only to purpose rent (`not_applicable` on a sale); never
  assumed. Saving never requires complete data: a rental `price_basis` without its `rental_period`, or a period
  alone, saves like any other partial state (autosave is field by field). That a rent priced per rental period names its
  period, and every `required_for_publication` field, is a **publication rule** checked in Story 2.15.
- Errors are reported per registry field id, as codes only: a submitted value is never echoed and never logged.

### Provenance

`origin` is an argument chosen by the calling layer: the owner API passes `manual`, the future Assistant passes `ai`. It
is never read from a client payload or model output (`origin` and `confirmed` in a body are `unknown_field`). A field
written with `manual` is stored `confirmed: true`; one written with `ai` is stored `confirmed: false` until the owner
confirms it. Confirming is a manual save: saving the stored value of a field whose provenance is not manual and
confirmed is a CHANGE (provenance is written, the revision increases and a `ListingRevision` records the field id and
value, without a model-inputs event because the value did not change); a manual save that changes nothing and finds
the field already manual and confirmed stays a no-op. Whether the Assistant may overwrite a manual confirmed value is
decided in Epic 5: the service currently allows it and stores `confirmed: false`. The wire `provenance` maps field id to `{origin, confirmed}` for fields that are set.

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
| `PATCH /api/v1/listings/{id}` | body strictly `{base_revision, fields}`; `200` with the Listing; `409 stale_revision` (below); `400` field codes keyed by registry id; `404` for non-owners; `403` Suspended; CSRF; no-store |
| `GET /api/v1/me/listings` | own listings, `status` filter, `page_size`, `cursor`; `{next, previous, results}` (`previous` is always null); bad cursor or status is `validation_error`; no-store |
| `GET /api/v1/registry` | public Field registry (`docs/registry.md`) |

## The 409 body

`409` is the standard error envelope with code `stale_revision` plus the current Listing under a top-level `current` key:
`{"error": {code, message, fields, request_id}, "current": <Listing resource>}`. The client reloads from `current` and
reapplies its edit with `current.revision` as the new `base_revision`. The OpenAPI response schema is `ListingConflict`
(`tests/api/test_listings_api.py` asserts it). A non-owner never receives a conflict body: ownership is judged first
(`404`). The same rule covers simultaneous sessions (one of two edits with the same base revision wins, the other is a
409; `tests/listings/test_apply_updates.py` runs this with threads) and, once Story 2.15 widens `EDITABLE_STATUSES`,
edits of Published Listings.

## Wire shape (AD-24)

`{id, status, visibility, revision, registry_version, fields: {purpose, category, subtype, title, description,
asking_price, price_basis, rental_period}, provenance: {<field id>: {origin, confirmed}}, media: [], amenities: []}`.
`visibility` is `private` for Drafts (`listings.lifecycle.Visibility`, extended by Story 2.12). `fields` is keyed by flat
registry ids and returns `null` for unknown; `asking_price` is a decimal string. No owner or other internal column is returned. `tests/api/test_listings_api.py`
asserts the shape and the OpenAPI schema.

## Adding fields later

1. Add the field to `apps/catalog/registry/fields.yaml`, raise `version`, run `scripts/registry_lock.py` (`docs/registry.md`).
2. Add the storage (a column or a detail table) in `listings` with a migration, expose it in `selectors.view_of` as a flat
   registry id (add it to `selectors.view_of`, `validation.EDITABLE_FIELDS` and the `apply_updates` storage mapping), and
   accept it through the revision-checked edit service `apply_updates` (Story 2.2).
3. Serializers take enums from `catalog.registry`; never hand-copy values. New audit metadata is registered in
   `listings.apps` with enumerations only.
