# Listings

`apps/listings` owns the canonical Listing. Story 2.1 adds starting a Draft, Story 2.2 conflict-safe editing of title,
description and price, Story 2.3 the area and its unit, Story 2.4 the House and Apartment details; later stories add more fields, media and publication (`_bmad-output/implementation-artifacts/epic-2-context.md`).

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
- `ListingArea` (Story 2.3, migration `0003_area`): one row per Listing, created lazily by `apply_updates` the first
  time an area field changes (never by `create_draft`): `area_value` NUMERIC(14,4), `area_unit`, `marla_basis`
  NUMERIC(6,2) (square feet per marla), `area_sqft` NUMERIC(20,4) (derived), `updated_at`, all nullable. CHECKs generated
  from the registry (`models.area_constraints`): value within the bounds, unit and basis in their enums, a basis only
  while the unit is marla or kanal, and `area_sqft` null unless the area is computable (a value, a unit and, for marla
  and kanal, a basis). `tests/listings/test_constraints.py` fails when migration 0003 and the registry disagree.
- `ListingResidential` (Story 2.4, migration `0004_residential`): one row per Listing holding the House and Apartment
  details, one nullable typed column per registry field (integers `IntegerField`, yes/no answers nullable booleans,
  enums text, `covered_area_sqft` NUMERIC(14,4), `maintenance_charges` NUMERIC(14,2)) and `updated_at`. **The row is
  created lazily, only when a residential field is first saved with a non-null value**: never by `create_draft`, by a
  read, by a clear (`null`) or by an edit of other fields. Once it exists, clearing a field sets its column NULL and the
  row stays. Unanswered is always `NULL`, never `0` or `false`; booleans have no default. CHECKs generated from the
  registry (`models.residential_constraints`, named `listings_res_<field>_range` or `_valid`): integer, decimal and
  money bounds, enum sets, plus `listings_res_floor_within_building` (floor not above the building's floors when both
  are set). `tests/listings/test_constraints.py` style drift tests in `tests/listings/test_residential.py` fail when
  migration 0004 and the registry disagree.
- `ListingRevision`: `listing`, `revision`, `origin` (`manual` or `ai`), `actor_account_id`, `changes` (the registry
  fields set at that revision and their new values: strings, JSON integers and booleans, or null), unique (`listing`, `revision`). Edits are
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
that is editable (`title`, `description`, `asking_price`, `price_basis`, `rental_period`, `area_value`, `area_unit`,
`marla_basis` and the residential details below): other registry ids such as `purpose`, and the derived `area_sqft`, are `not_editable`, unknown ids (`owner_account_id`, `status`, `origin`, ...) are `unknown_field`
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

`STORAGE` is the single map from editable registry field id to its table (`listing`, `terms`, `area` or `residential`).
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

### Area and units (Story 2.3)

- `area_value` (registry type `decimal`, scale 4): text only (a JSON string; JSON numbers, floats, booleans, commas,
  exponents, whitespace, zero-padding such as `0010` and non-ASCII digits are refused, `invalid`). Valid text is
  ASCII `^\d{1,10}(\.\d{1,4})?$` without leading zeros; a well-formed number that is zero, negative, below `0.0001`,
  above `9999999999.9999` (so also more than ten whole digits) or has more than four decimals is `invalid_area`. Stored
  NUMERIC(14,4) and serialised exactly as stored, `"10.0000"` (the client formats it).
- `area_unit` (`sqft`, `sqyd`, `marla`, `kanal`, `acre`): anything else is `invalid_choice`.
- `marla_basis` (`"225"`, `"250"`, `"272.25"`: square feet in one marla; a kanal is 20 marla so it applies to `marla`
  and `kanal`): only those exact strings are accepted (`invalid_choice` otherwise, JSON numbers included). Stored as
  NUMERIC(6,2) and mapped back to the enum string on the wire. Cross-field, judged on the RESULTING state: a basis while
  the resulting unit is not marla or kanal is `not_applicable` on `marla_basis`. When an edit changes the unit away from
  marla or kanal while a basis is stored and the request does not mention the basis, the service clears it (stored data
  stays consistent); the response shows `null`, the provenance entry is removed and the revision row records
  `marla_basis: null`.
- `null` clears any area field. A value without a unit, or a unit without a value, is allowed in a Draft; completeness
  (`area_value` and `area_unit` are `required_for_publication`) is a Story 2.15 matter.
- `area_sqft` is DERIVED: registry `derived: true`, in `listings.fields.DERIVED` (never in `EDITABLE_FIELDS`), rejected on
  input as `not_editable`, never given provenance. `apply_updates` recomputes it inside the same transaction whenever the
  value, unit or basis changes, with exact `Decimal` arithmetic (`listings.area.normalised_sqft`, no float anywhere):
  `sqft` value, `sqyd` value x 9, `acre` value x 43 560, `marla` value x basis, `kanal` value x 20 x basis, rounded half
  up to four decimals. A missing value or unit, or marla and kanal without a basis, give `null`. On every edit of a
  Listing that has an area row the figure is recomputed from the resulting state; when the stored `area_sqft` differs
  it is written and recorded in the revision as a normal change entry, so a stale or null stored figure is repaired
  by any save (even one that looks like a no-op, which then costs a revision).
- **An unknown basis does not block saving or later publication.** The Listing simply has `area_sqft = null`. Excluding
  such Listings from area-range filtering, and giving them no price estimate, is implemented by the later stories
  (search and valuation) by reading `area_sqft is null`; this story only guarantees the value is null, never a guess.
- `area_value`, `area_unit` and `marla_basis` are `valuation_input` fields: a change to any of them (including a basis
  cleared by a unit change) emits `listing.model_inputs_changed` after commit. `area_sqft` is not (it is computed from them).

### House and Apartment details (Story 2.4)

Registry version 4 adds the new types `integer` (`min`, `max`) and `boolean` and 30 fields, stored in `ListingResidential`
(`listings.fields.RESIDENTIAL`). All are class public and `ai_extractable`; applicability is by Subtype and is judged from
the Draft's identity, which never changes, so a field sent for another Subtype (or for a plot or commercial Draft, which
get their own details in Story 2.5) is `not_applicable` whatever its value.

- House and Apartment: `bedrooms` and `bathrooms` (0 to 50, `required_for_publication`, `searchable`, `valuation_input`),
  `construction_year` (1900 to 2100, `recommended` for both Subtypes, `valuation_input`), `condition` (`new`,
  `excellent`, `good`, `needs_renovation`; `valuation_input`), `furnishing` (`unfurnished`, `semi_furnished`,
  `furnished`; `searchable`), `parking_spaces` (0 to 50, `searchable`), `occupancy_status` (`owner_occupied`,
  `tenant_occupied`, `vacant`) and `covered_area_sqft` (decimal, scale 4, `0.0001` to `9999999999.9999`,
  `valuation_input`; always square feet, independent of `area_unit`, and NOT required to be below `area_sqft`, as
  units may be unknown or loose).
- House only: `total_floors` (1 to 200), `kitchens` (0 to 50), `construction_status` (`completed`, `under_construction`,
  `grey_structure`), optional room counts `drawing_rooms`, `dining_rooms`, `tv_lounges`, `study_rooms`, `prayer_rooms`,
  `store_rooms`, `laundry_rooms`, `powder_rooms`, `servant_quarters` (0 to 50) and optional yes/no `basement`.
- Apartment only: `floor_number` (0 to 200, 0 is the ground floor), `building_floors` (1 to 200), yes/no `lift`,
  `backup_electricity`, `building_security` (recommended), optional yes/no `balcony_or_terrace`, `building_lobby`,
  `intercom`, and optional money `maintenance_charges` (PKR per month, `0` to `999999999999.99`, the money rules of
  `asking_price`, `invalid_price`: a decimal string or a bare JSON integer is accepted, floats and booleans never; zero is allowed).

Validation (`listings.validation`): an integer is accepted only as a JSON integer (a string, a float such as `3.0`, a
boolean, a list or an object is `invalid`) and outside the registry bounds is `invalid_range`; a boolean only as JSON
`true` or `false` (`1`, `"yes"` and the like are `invalid`); enums are `invalid_choice` for a string that is not a value and `invalid` for a non-string (a number, boolean, list); the covered area follows the
decimal rules (`invalid`, `invalid_area`); `null` clears. Applicability is judged before the value, but a `null` is never judged for applicability: on a field that
does not apply to the Subtype it is a `200` no-op when nothing is stored (no revision, no row), and it clears a value
stored under an older registry. A stored value outside today's bounds that is sent again unchanged is `invalid_range`
(stored values are revalidated only when sent). One cross-field rule,
on the RESULTING state and only when the request touches `floor_number` or `building_floors`: for an apartment a
`floor_number` above `building_floors` (both set) is `invalid_floor` on `floor_number` and ALSO on `building_floors` when the request sent it (so the client finds the code
under a field it sent); a failure on either field's own
rules suppresses it, and stored data is not re-judged by unrelated edits. Both new codes are in the kernel messages
catalogue.

Wire and revisions: integers are JSON integers, booleans JSON booleans, the covered area a string with four decimals,
the charges a string with two decimals; every unknown or not-applicable field is `null`. `changes` in a `ListingRevision`
uses the same native values. A change to a `valuation_input` field (bedrooms, bathrooms, construction year, condition,
covered area) emits one `listing.model_inputs_changed` per save, after commit; a clear of one counts as a change.
`PATCH` still needs a current `base_revision` (409 with `current` otherwise).

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

Wire order of `fields`: identity, the editable fields in `STORAGE` order, `area_sqft` last.
`{id, status, visibility, revision, registry_version, fields: {purpose, category, subtype, title, description,
asking_price, price_basis, rental_period, area_value, area_unit, marla_basis, <the 30 residential details>, area_sqft}, provenance: {<field id>: {origin, confirmed}}, media: [], amenities: []}`.
`visibility` is `private` for Drafts (`listings.lifecycle.Visibility`, extended by Story 2.12). `fields` is keyed by flat
registry ids and returns `null` for unknown; `asking_price`, `area_value` (four decimals), `marla_basis` (`"225"`,
`"250"`, `"272.25"`) and `area_sqft` (four decimals, `null` when not computable) are strings, never numbers; the residential integers and yes/no answers are JSON integers and booleans. Provenance covers
the editable fields, not `area_sqft`. No owner or other internal column is returned. `tests/api/test_listings_api.py`
asserts the shape and the OpenAPI schema.

## Adding fields later

1. Add the field to `apps/catalog/registry/fields.yaml`, raise `version`, run `scripts/registry_lock.py` (`docs/registry.md`).
2. Add the storage (a column or a detail table) in `listings` with a migration, expose it in `selectors.view_of` as a flat
   registry id (add it to `selectors.view_of`, `validation.EDITABLE_FIELDS` and the `apply_updates` storage mapping), and
   accept it through the revision-checked edit service `apply_updates` (Story 2.2).
3. Serializers take enums from `catalog.registry`; never hand-copy values. New audit metadata is registered in
   `listings.apps` with enumerations only.
