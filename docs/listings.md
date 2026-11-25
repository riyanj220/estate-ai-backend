# Listings

`apps/listings` owns the canonical Listing. Story 2.1 adds starting a Draft, Story 2.2 conflict-safe editing of title,
description and price, Story 2.3 the area and its unit, Story 2.4 the House and Apartment details, Story 2.5 the Plot and Commercial details, Story 2.7 the location and the private address, Story 2.9 the image upload, Story 2.10 the cover, order and media endpoint; Story 2.12 the visibility policy and the public projection (`docs/visibility.md`); later stories add more fields, media and publication (`_bmad-output/implementation-artifacts/epic-2-context.md`).

## Model (`listings.models`, written only by `listings.services`)

- `PropertyListing`: `id` (UUID), `owner_account_id` (a plain indexed UUID column, no foreign key into `identity`),
  `status` (`draft`, `published`, `archived`, `sold`, `rented` or `removed`; the code-defined enum `listings.lifecycle.ListingStatus`, with a CHECK; only `listings.publication.publish` sets `published` (Story 2.15), only `listings.status.change_status` sets `archived`, `sold` and `rented` (Story 2.17), nobody sets `removed` yet (moderation does later); visibility fixtures may still write any of them through the model), `restricted` (boolean, default false; set by moderation in a later epic, fixtures only until then), `restriction_reason` (nullable text of at most 200 characters, the reason for a restricted or removed Listing: written only by moderation later and by test fixtures now; owner resource only, never in a public shape), `revision` (starts at 1),
  `registry_version`, the registry fields `purpose`, `category`, `subtype` as columns with CHECK constraints generated
  from the taxonomy (including that the Subtype belongs to the Category), `title` and `description` (nullable, empty is
  `null`, length CHECKs from the registry), `provenance` (JSON, registry field id to `{"origin", "confirmed"}`, set
  fields only), `created_at`, `updated_at`. Index: (`owner_account_id`, `status`, `-created_at`).
- `ListingTerms`: one row per Listing (`listing` one-to-one), created with the Draft and lazily by `apply_updates` for
  older Drafts: `asking_price` NUMERIC(14,2) nullable (CHECK between the registry bounds), `price_basis` and
  `rental_period` nullable (CHECKs generated from the registry values), `updated_at`. Money is NUMERIC, never a float.
  **Story 2.6 (migration `0006_terms`)** adds the sale and rent terms as typed nullable columns on this same row (no new
  table, no row created by a clear or a read): `price_negotiable` and the rent booleans (`pets_allowed`,
  `utility_*_included`) nullable booleans; `security_deposit`, `advance_rent` NUMERIC(14,2); `availability_date` a SQL
  `DATE` (a calendar date, never a timestamp); `minimum_lease_months`, `notice_period_days` SMALLINT;
  `possession_availability`, `occupant_restrictions` text enums. CHECKs are generated from the registry
  (`models.terms_constraints`, `listings_terms_<field>_range|_valid`; the date window is a CHECK too), and the earlier
  three keep their names. Unanswered is `NULL`, never 0, false or empty.
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
  **Story 2.5 (migration `0005_plot_commercial`) widens it**: this table now holds the attributes shared by residential,
  plot and commercial units (the model keeps its name): Shops and Offices use its `condition`, `furnishing`,
  `parking_spaces`, `occupancy_status`, `floor_number`, `building_floors`, `lift` (offices), `building_security`,
  `building_lobby` and `maintenance_charges` columns, and the new utility booleans `electricity`, `water` and `gas`
  (plots and commercial units) are three more columns here. The floor CHECK is unchanged. It is created lazily on the
  first non-null value of any of these, whatever the Subtype.
- `ListingPlot` (Story 2.5, migration `0005_plot_commercial`): one row per Listing for the Plot, Commercial Plot and
  Plot File details (`related_name="plot"`): NUMERIC(10,2) feet (`plot_width`, `plot_depth`, `road_width_ft`), nullable
  booleans and text enums, `updated_at`. Created lazily on the first non-null plot value (never by `create_draft`, a
  read, a clear or an unrelated edit); unanswered is `NULL`. CHECKs generated from the registry
  (`models.plot_constraints`, `listings_plot_<field>_range|_valid`). The column allows the union of the enum values; the
  by-Subtype vocabulary (a Plot File only `file_only`) is enforced by the service.
- `ListingCommercial` (Story 2.5): the same for Shop and Office details (`related_name="commercial"`;
  `models.commercial_constraints`, `listings_com_<field>_range|_valid`).
- `ListingLocation` (Story 2.7, migration `0010_location`, `related_name="location"`): one row per Listing, created
  lazily on the first non-null write (never by `create_draft`, a read, a clear or an unrelated edit): `city` and
  `locality` (plain UUID columns named `city_id` and `locality_id`, no key into `catalog`), `society`, `phase`,
  `sector`, `block` (text, 60), `updated_at`, all nullable. Index (`city_id`, `locality_id`) for later search; CHECK
  that a locality needs a city. That the locality belongs to the city is a catalog fact the service judges.
- `ListingPrivateAddress` (Story 2.7, `related_name="private_address"`): a SEPARATE table, one row per Listing,
  created lazily the same way: `street` (120), `house_number` (30), `latitude` and `longitude` NUMERIC(9,6), all
  nullable. CHECKs: the registry bounds (`listings_addr_<field>_range`) and `listings_addr_coordinates_paired`
  (both or neither). It is read only for the owner's own resource (`selectors.get_owned_listing`, `list_owned`, the
  edit service) and never joined into any public selector or projection.
- `ListingRevision`: `listing`, `revision`, `origin` (`manual` or `ai`), `actor_account_id`, `changes` (the registry
  fields set at that revision and their new values: strings, JSON integers and booleans, or null for public fields;
  for a field of class `private`, or one the registry does not define, only `true` (set) or `null` (cleared), never
  the value), unique (`listing`, `revision`). Edits are
  recorded by these rows; there is no separate audit action for them.

- `ListingImage` (Story 2.9, migration `0007_images`; Story 2.10, migration `0008_cover`): `id` (UUID), `listing`,
  `asset_id` (a plain UUID of the `kernel.MediaAsset`, unique: an asset belongs to at most one image), `position`
  (0-based and contiguous per Listing, 0..n-1; unique per Listing with the check deferred to commit), `is_cover` (at
  most one per Listing, a partial unique index), `created_at`. The file itself is the kernel's; see `docs/media.md`.
  Written only by `listings.services`.

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
2. The status gate (Story 2.17): a status outside `lifecycle.EDITABLE_STATUSES` (`draft`, `published`, `archived`; sold,
   rented and removed are view only) and a restricted Listing (`restricted` true, any status) are `forbidden`. While the
   Listing is **Published** two more rules apply (see "Lifecycle" below): the identity fields are `not_editable`, and the
   result must still pass the publication completeness rules (`400 validation_error`, nothing written).
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

`STORAGE` is the single map from editable registry field id to its table (`listing`, `terms`, `area`, `residential`, `plot`, `commercial`, `location` or `address`).
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

### Plot and Commercial details (Story 2.5)

Registry version 5 adds the registry flag `seller_declared` and the map `allowed_values_by_subtype` (see
`docs/registry.md`) and these fields. Applicability is by Subtype and judged from the Draft's identity (`not_applicable`
otherwise, whatever the value; `null` on a field that does not apply is a `200` no-op, as in Story 2.4).

- **Shared with residential units, widened** (same ids and columns): `condition`, `furnishing` (the frontend labels it
  fit-out for commercial), `parking_spaces`, `occupancy_status`, `floor_number`, `building_floors` for Shop and Office
  too; `lift` for Office (and Apartment); `building_security` for Shop and Office; `building_lobby` and
  `maintenance_charges` for Shop and Office. The floor rule (`invalid_floor` on both sent floor fields) applies to
  every Subtype where both floors apply. Known limitation: a registry field has one requirement level (no
  per-Subtype levels yet), so `building_floors` and `water` are `recommended` for commercial Subtypes too.
- **Utilities** `electricity` (recommended), `water` (recommended), `gas` (optional): yes/no, all plot and commercial
  Subtypes, stored in the unit-details table.
- **Plot, Commercial Plot and Plot File** (`ListingPlot`): `plot_width`, `plot_depth` (feet, `0.01` to `99999.99`),
  `road_width_ft` (`1.00` to `999.99`), `corner_plot`, `sewerage` (yes/no), `possession_status`, `balloting_status`,
  `development_status`, `approval_authority` (enums, all **seller-declared** and not `ai_extractable`), optional `park_facing`, `main_boulevard`,
  `boundary_wall`, `orientation`, and `plot_file_status` (seller-declared, Plot File only).
- **Shop and Office** (`ListingCommercial`): `business_use`, `road_access` (enums), `frontage_ft` (Shop only, `0.01`
  to `9999.99`), optional `ceiling_height_ft` (`1.00` to `99.99`), `power_load_kw` (`0.01` to `99999.99`),
  `three_phase_electricity`, and Office only `conference_rooms` (0 to 50) and `business_centre_facilities`.
- **Plot File vocabulary**: `possession_status` has three values; a Plot File allows only `file_only`, the other plots
  `possession_ready` and `possession_pending`. Anything else for the Draft's Subtype is `invalid_choice`. A Plot File is
  therefore never presented as possession-ready. The rule lives in the registry map, not in code.
- **Seller-declared**: possession, balloting, development, approval authority and plot file status are the owner's own
  claims. The registry marks them (`seller_declared: true`, served) so a display or search layer shows them as
  "Seller-declared" and never as a platform badge or verified fact.
- **Unsupported attributes** such as `installment_available`, `down_payment`, `title_document_type`,
  `mortgage_declaration` and `approval_reference_number` are not in the registry, so they are `unknown_field` under
  `non_field_errors` like any unknown id, and nothing is written (they arrive in Epic 11).

Validation: the feet and kilowatt fields are strict decimal text with the registry scale (`^\d{1,10}(\.\d{1,2})?$`
shape, no leading zeros, exponent, comma, spaces or JSON numbers: `invalid`); a well-formed value that is zero,
negative, below the minimum, above the maximum or has too many decimals is the new code `invalid_measure` (unit-neutral text in the
kernel messages catalogue; the registry field's `error_code` decides, and the two area fields keep `invalid_area`). Values are stored and served at their stored scale
(`"35.00"`, never exponent form). Integers, booleans and enums follow Story 2.4; a stored value outside today's rules
that is sent again unchanged is rejected with the same code (stored values are revalidated only when sent, and only
when the request touches the floor fields is the floor rule judged).

Valuation inputs: `plot_width`, `plot_depth`, `road_width_ft`, `corner_plot`, `development_status`, `frontage_ft`
(and `condition`, already). A change to one emits one `listing.model_inputs_changed` after commit; the other new fields
emit nothing.

### Sale and rent terms (Story 2.6)

Registry version 6 adds the `date` registry type and these fields, stored on `ListingTerms`. Applicability is by
**purpose**, judged from the Draft's identity (the purpose is immutable, so a Draft can never hold the terms of the
other purpose); a wrong-purpose field is `not_applicable` whatever the value, and `null` on a field that does not apply
is a `200` no-op (nothing is written, the revision does not move; a stored value of the other purpose, which only a direct
database write could leave (there is no purpose CHECK), can still be cleared by `null`).

- **Both purposes**: `price_negotiable` (yes/no, optional).
- **Sale only**: `possession_availability` (`available_now`, `within_one_month`, `within_three_months`,
  `after_three_months`; recommended; **seller-declared**, so never shown as verified and not `ai_extractable`).
- **Rent only**: `security_deposit` (money, `0` to `999999999999.99`, recommended), `availability_date` (a date,
  recommended), `advance_rent` (money, optional), `minimum_lease_months` (1 to 120), `notice_period_days` (0 to 365),
  `utility_electricity_included`, `utility_gas_included`, `utility_water_included`, `utility_internet_included`,
  `pets_allowed` (yes/no, all optional) and `occupant_restrictions` (`no_restriction`, `families_only`,
  `bachelors_only`). For rent the `asking_price` is the rental amount and `rental_period` is required for publication
  and **never assumed**: a rent Draft saves and is served with `rental_period` null until the owner chooses one.
  `furnishing` and `maintenance_charges` keep their Story 2.4 and 2.5 definitions and Subtype applicability;
  maintenance charges for rent houses stay a later registry addition. No relation is checked between the deposit, the
  advance and the rent.
- **Dates**: `availability_date` accepts past dates back to 2000-01-01 on purpose (no check against today). JSON text `YYYY-MM-DD` only, in ASCII digits, naming a real calendar date (`2027-02-30`, `2027-3-1`,
  `20270301`, a timestamp, other digits and any non-text value are `invalid`); a real date outside the registry
  window `2000-01-01` to `2100-12-31` is `invalid_date`. A date is stored as SQL `DATE` and served as the same text
  (`"2027-03-01"`), in revisions too; no time zone or time of day is ever applied, so the value cannot shift by a day.
  The message for `invalid_date` is neutral catalogue text that names no value.
- **Money, integers, booleans, enums**: as `asking_price` (`invalid_price`, now worded without "above zero" because a
  deposit and the advance rent may be `0`; the text is deliberately neutral, "within the allowed range"), Story 2.4 integers (`invalid`, `invalid_range`), booleans and enums (`invalid_choice`).
- **Unsupported (deferred)** terms `payment_type`, `down_payment`, `total_installment_price`, `monthly_installment`,
  `remaining_installments`, `ownership_document_type`, `title_document_type`, `approval_information`,
  `installment_available` and any other id outside the registry are `unknown_field` under `non_field_errors`; nothing
  is written (Epic 11).
- None of the new fields is a `valuation_input`, so a terms edit never emits `listing.model_inputs_changed`; an edit
  that also changes a model input emits exactly one event after commit, as before.
- A stored value outside today's rules (an older registry's wider window or range) is never altered by an unrelated
  edit, and is rejected with the same code if the request sends it again unchanged.

### Location and private address (Story 2.7)

Registry version 7 adds the `reference` type (`reference: city | locality`) and ten fields, all applicable to every
purpose, category and Subtype, in `section: location`.

- **Public** (class `public`): `city` and `locality` (`required_for_publication`, `searchable`; the uuid of an ACTIVE
  catalog city or locality; the id is read by the catalog's one `parse_uuid` in any common form (any case, braced,
  hyphenated or not) and stored and served as canonical lowercase hyphenated text; a name, a number or any other
  text, an unknown, inactive or malformed id is `invalid`; **a NEW value must be active, but re-sending the STORED id
  of a city or locality that has since been deactivated is accepted** while only other fields change, and a stored
  locality that is no longer active cannot follow a change of city) and `society`, `phase`, `sector`, `block` (text, up to 60 characters,
  optional, `ai_extractable`, trimmed, empty is null). They are part of the public location.
- **Private** (class `private`; optional; never searchable, sortable, a valuation input or `ai_extractable`): `street`
  (text, 120), `house_number` (text, 30), `latitude` (decimal, -90 to 90) and `longitude` (decimal, -180 to 180), both
  with six decimals, sent and served as decimal strings (`"24.860000"`, a leading minus allowed because the registry
  marks them `signed: true`, never a JSON number). A value outside the range or with more than six decimals is
  `out_of_range`; anything that is not a plain decimal is `invalid`. `-0` and `-0.000000` are stored and served as
  `"0.000000"`, never a negative zero. **Both or neither**: judged on the resulting state; the code is `invalid` on
  the field that ends up empty, which is the field the client SENT as `null` when it is the one being cleared, else the
  one it left out (`{"latitude": v}` alone is `invalid` on `longitude`; clearing only one of two stored values is
  `invalid` on the cleared one; clear both together).
- **The locality rule**: a locality must belong to the Listing's city, judged on the resulting pair. A locality of
  another city is `invalid` with the message code `locality_not_in_city` on `locality`; a locality while no city is
  saved is `invalid` on `locality`. **Changing or clearing `city` without sending a `locality` clears the stored
  locality in the same write** and reports it among the changed fields (and in the revision `changes`), so a stale pair
  is never stored. Sending a new city with a locality of the new city is fine.
- Text fields use the existing text rules and the shared contact-detail check (`contains_contact_details`, `too_long`
  and so on); messages are static and never echo a value.
- **Accepted window**: the city and locality are resolved against the catalog while the Listing row is locked, but the
  catalog row itself is not locked. A locality deactivated between that check and the commit is saved (the write is
  still a valid choice made a moment earlier, and a stored inactive id remains re-saveable, see above).
- `selectors.view_of` leaves the private fields out unless called with `with_private=True` (keyword-only, default
  false); only the owner's own reads and the edit service pass it. `ListingView` and the `StaleRevision` it rides in
  redact private values in `repr` and `str`.
- Written only by `apply_updates` (same `base_revision`, `409 stale_revision`, provenance, one revision row;
  `null` on any of them is a harmless no-op while unset; a clear never creates a row).
- **Privacy (AD-5, AD-26)**: the private fields are in the OWNER's Listing resource only (`private, no-store`, `404`
  for everyone else, administrators and Visitors included). `PublicListingView.fields` never carries a private-class
  field, and `selectors.public_view_of` does not even read the private table. Private values never appear in logs, error
  bodies, validation messages, audit or revision rows (a revision records `true` for set and `null` for cleared),
  events (`listing.model_inputs_changed` carries only the Listing id; none of these fields is a model input), OpenAPI
  examples or any URL. `tests/listings/test_location.py` has canary tests for each.

### Amenities and seller-declared nearby facilities (Story 2.8)

- One registry field, `amenities` (registry version 8, type `amenities`, section `amenities`, class public, optional,
  every purpose and Subtype, `searchable`, a `valuation_input`, not sortable). Its value is a JSON object
  `{<catalogue code>: "present" | "none"}`; the codes come from the `catalog` amenity catalogue
  (`GET /api/v1/reference/amenities`, `docs/reference-data.md`), never from the client.
- **Unanswered is a third state, and it is not stored.** A code that is not in the map is unanswered. It is never read
  as `none` and never has a row. `none` is an answer the owner gave ("this property has no lift"), also for `nearby`
  entries ("no school nearby").
- A PATCH value REPLACES the whole map (so the revision check keeps it conflict-safe: two editors cannot merge
  silently). `null` and `{}` both clear it. Refused as `invalid` on `amenities`, nothing written: a value that is not
  an object, more than 40 entries, a key that is not an active catalogue code, a state other than the exact strings
  `present` and `none`. One entry per code is inherent to a JSON object (a repeated key in the raw body is the
  parser's last-wins). An entry the Listing already stores with the SAME state is kept even if the catalogue has
  deactivated its code since, so a client re-sending what it read is not locked out (a new or changed entry must use an
  active code).
- Stored in `listings_listingamenity` (listing FK, `amenity_code` text, `state` CHECK `present`/`none`, unique
  `(listing, amenity_code)`, index on `(amenity_code, state)` for later search). Written only inside `apply_updates`, in
  the same transaction and under the same listing row lock and `base_revision` check as every other field: the rows are
  replaced as a set. Provenance is recorded under `amenities` like any field; the revision row records the map of codes
  and states (not private).
- Read: the owner resource and `PublicListingView.fields` carry `fields.amenities` as the map of ANSWERED codes only,
  or `null` when nothing is answered. Nothing else exists per entry: no distance, no verification flag, no score and no
  computed value in any response, projection, OpenAPI, event or log.
- **Seller-declared nearby facilities.** The catalogue entries of `kind` `nearby` (mosque, park, gym, school, hospital)
  are the seller's own claim. This is a property of the catalogue kind, not of the stored entry: clients must label
  every `nearby` entry "Seller-declared" and show no distance. EstateAI does not verify them. Unmatched mentions of other
  facility types are not stored (a later AI story flags them).
- A change to the map emits `listing.model_inputs_changed` after commit (the event carries only the Listing id):
  `amenities` is deliberately flagged `valuation_input`.
- Deactivated stored code: re-sending a stored entry unchanged is accepted; changing its state is `invalid`; to drop
  the answer the owner omits the code from the map.
- `selectors.public_view_of` reads the map with one query per Listing. That N+1 is deferred until a public list exists
  (the owner's own list already batches it with `amenities_by_listing`).
- The top-level `amenities` list that the Listing resource carried as an empty placeholder since Story 2.1 is gone: the
  map lives in `fields.amenities`.

### Provenance

`origin` is an argument chosen by the calling layer: the owner API passes `manual`, the future Assistant passes `ai`. It
is never read from a client payload or model output (`origin` and `confirmed` in a body are `unknown_field`). A field
written with `manual` is stored `confirmed: true`; one written with `ai` is stored `confirmed: false` until the owner
confirms it. Confirming is a manual save: saving the stored value of a field whose provenance is not manual and
confirmed is a CHANGE (provenance is written, the revision increases and a `ListingRevision` records the field id and
value, without a model-inputs event because the value did not change); a manual save that changes nothing and finds
the field already manual and confirmed stays a no-op. Whether the Assistant may overwrite a manual confirmed value is
decided in Epic 5: the service currently allows it and stores `confirmed: false`. The wire `provenance` maps field id to `{origin, confirmed}` for fields that are set.

## Lifecycle after publication (Story 2.17)

Statuses: `draft`, `published`, `archived`, `sold`, `rented`, `removed`. `services.apply_updates` and the image services
edit a Draft, a Published and an Archived Listing; `sold`, `rented`, `removed` and any restricted Listing are view only
(`403 forbidden`, nothing written). The owner sees their own Listing in every status (`policy.OWNER_VISIBLE_STATUSES`),
removed and restricted ones included; the public sees only Published, not restricted, Active-owner Listings (unchanged).

**Transitions** (`listings.status.change_status(actor, listing_id, base_revision, target)`, the only writer of
`archived`, `sold` and `rented`; guard: `tests/architecture/test_publication_single_writer.py`):

| From | May move to |
| --- | --- |
| `published` | `archived`, `sold`, `rented` |
| `sold`, `rented` | `archived` |
| `draft` | nothing (`409 conflict`); a Draft is deleted or published |
| `archived` | nothing here (`409 conflict`); exactly one outgoing path, `publication.republish` (Story 2.18, below) |
| `removed` | nothing (`403 forbidden`: moderation owns it) |

Every other move (the same status, to `published`, to `removed`, to `draft`) is `409 conflict` (a target outside
archived, sold, rented is `400 validation_error` under `status`) and writes nothing. One transaction, account lock then
Listing lock: `base_revision` against `revision` (`409 stale_revision` + `current`, checked first), the account-state and
restriction refusals, the table, then status + `revision + 1`, a `ListingRevision` (`changes: {"status": <new>}`),
audit `listing.status_changed` (`from`, `to`; the Listing id is the target), and the domain event `listing.status_changed`
(`ListingStatusChanged`: listing id, account id, new status) as the last statement, after commit. No quota, no
`PublicationEvent`, no idempotency key: a repeat is a clean `409 conflict`. There is no way back to `published` here
(`publication.republish`, Story 2.18, is the only way out of Archived).

**Who may do what** (`docs/account-states.md`): Active and Email-unverified owners archive, mark sold and mark rented
and edit; a Suspended owner may only archive (sold and rented are `403 forbidden`; edits and image changes are
`forbidden` as before) and may still read everything and the counts; a Deactivated account has no session. A
**restricted** Listing may still be archived by its owner (withdrawing is never refused; the restriction stays in force)
but not marked sold or rented and not edited; a **removed** one cannot be changed at all (`403 forbidden`). This is
decided from the locked rows after the stale check.

**Editing a Published Listing** consumes no quota and writes no `PublicationEvent`; it bumps the revision with a revision
row as any edit does and emits `listing.model_inputs_changed` when a `valuation_input` field changed. Two extra rules:

- **Identity fields are locked while Published.** The set is the registry flag `quota_identity` (read from the registry,
  never listed in code): `purpose`, `category`, `subtype` (never editable at all), `city`, `locality`, `area_value`,
  `area_unit` and `marla_basis`. An update that would change the stored value of one (including clearing it, or the
  implicit clearing of the locality by a city change) is `not_editable` on that field, reported together with the other
  failing fields. Sending the stored value again is accepted and changes nothing. They are editable on a Draft and on an
  Archived Listing (identity changes are re-checked, and counted, at republication, Story 2.18). `price_basis` stays
  editable.
- **A Published Listing stays complete.** After the edit is applied inside the transaction, the result is judged with the
  same function as publication (`listings.completeness.problems_of`, shared with `publication`, no copy), but the edit is
  only answerable for what it touches: a problem is reported when (a) this edit changed that field, (b) a
  `required_for_publication` item is missing, or (c) the Listing has no live image (`images_required`). A stored value
  this edit did not change (a city or locality deactivated since, a rule tightened on another field, an unconfirmed
  AI value) never blocks it, so a title or price edit of such a Listing still works. Problems are `400 validation_error`,
  all at once, keyed like publication; the transaction is rolled back, revision unchanged. An edit that changes nothing
  returns the current view and is not re-judged. An edit of an Archived Listing is validated field by field like a Draft edit (completeness is
  enforced again at republication).
- **Images**: upload, remove, reorder and cover change are allowed for Published and Archived Listings (same cap and
  cover rules). A Published Listing keeps at least one LIVE image (a link whose asset row exists): `remove_image` decides
  it before deleting anything and `set_image_order` after ghost links are repaired (that repair rolls back too); both are
  `400 validation_error` with `images: images_required`, nothing changed. Racing removals are serialised by the Listing
  lock: exactly one wins. A Published Listing whose only links are ghosts can still take a real upload.
- **Sold is for a sale, rented for a rental.** `sold` needs purpose `sale` and `rented` purpose `rent` (the registry has
  exactly those two purposes), else `409 conflict`, nothing written. Archive has no purpose rule.

**Reads.** `selectors.list_owned` accepts every status (`status` filter, cursor bound to it) and
`selectors.owner_counts(actor)` returns `{draft, published, archived, sold, rented, removed}` (every key, zero when none;
the caller's own Listings only; same guard as the list). The owner resource gained `restricted` (boolean) and
`restriction_reason` (text or null). `preview_of` works for the owner's Listing in every status (same projection as the
public one, no `restriction_reason`). Interactions: only Drafts count toward the 20 open Drafts, are retention
candidates and can be deleted (`409 conflict` otherwise); publication of a non-Draft is `409 conflict`; the sample-data
reset removes Listings in any status.

A `publish` replay (same key, same body) returns the owner's CURRENT view of the Listing, so after an archive or sale it
shows that status: clients must read `status`, not assume `published`. Nothing is published again.

Deferred (see the spec's triage log): owner hard-delete of archived, sold and rented Listings; a restricted Draft keeps
its delete but cannot be edited.

New code or sentence: none (every refusal reuses `forbidden`, `conflict`, `stale_revision`, `validation_error`,
`not_found` and the field sentences `not_editable`, `images_required`, `required`, `invalid_choice`).

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
| `GET /api/v1/listings/{id}/preview` | owner only (own `draft` or `published`), `404` otherwise, `401` Visitor; the public projection plus `estimate: null`; no-store; counts as an open (below) |
| `DELETE /api/v1/listings/{id}` | owner only; `204`; a non-draft is `409 conflict`; `404` for non-owners, unknown ids and repeats; `403` Suspended; CSRF; no-store (below) |
| `POST /api/v1/listings/{id}/publish` | body strictly `{base_revision}`, header `Idempotency-Key` (required); `200` with the owner Listing (`status: published`); `400`, `401`, `403`, `404`, `409`, `422` (see "Publication" below); owner only; CSRF; no-store |
| `POST /api/v1/listings/{id}/republish` | body strictly `{base_revision}`, header `Idempotency-Key` (required); `200` with the owner Listing (`status: published`) plus `publication_kind` (`republish_counted` or `republish_free`); `400`, `401`, `403`, `404`, `409`, `422` (see "Republication" below); owner only; CSRF; no-store (Story 2.18) |
| `POST /api/v1/listings/{id}/status` | body strictly `{base_revision, status}` (`status`: `archived`, `sold` or `rented`); `200` owner Listing; `400`, `401`, `403`, `404`, `409` (`stale_revision` with `current`, or `conflict`); owner only; CSRF; no-store (Story 2.17) |
| `GET /api/v1/me/listings/counts` | `{draft, published, archived, sold, rented, removed}` for the caller; no-store; same guards as the list (Story 2.17) |
| `GET /api/v1/me/listings` | own listings, `status` filter, `page_size`, `cursor`; `{next, previous, results}` (`previous` is always null); bad cursor or status is `validation_error`; no-store |
| `GET /api/v1/me/publication-allowance` | owner allowance `{limit, used, remaining, period_start, resets_at, timezone}` (below); `401` Visitor and Deactivated (no session), `403` administrator; no-store |
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
asking_price, price_basis, rental_period, area_value, area_unit, marla_basis, <the 30 residential details, electricity, water, gas, the 14 plot details, the 8 commercial details, the sale and rent terms, city, locality, society, phase, sector, block and the private street, house_number, latitude, longitude, amenities>, area_sqft}, provenance: {<field id>: {origin, confirmed}}, media: [<ImageView, see "Images" below>]}`.
`visibility` is `public` only while the Listing really is public (published, not restricted, owner Active, derived from `listings.policy`), else `private` (`listings.lifecycle.Visibility`); who may see a Listing is `listings.policy.can_view`, see `docs/visibility.md`. `fields` is keyed by flat
registry ids and returns `null` for unknown; `asking_price`, `area_value` (four decimals), `marla_basis` (`"225"`,
`"250"`, `"272.25"`) and `area_sqft` (four decimals, `null` when not computable) are strings, never numbers; the residential integers and yes/no answers are JSON integers and booleans, and the feet and kilowatt fields are strings with two decimals. Provenance covers
the editable fields, not `area_sqft`. No owner or other internal column is returned. `tests/api/test_listings_api.py`
asserts the shape and the OpenAPI schema.

## Adding fields later

1. Add the field to `apps/catalog/registry/fields.yaml`, raise `version`, run `scripts/registry_lock.py` (`docs/registry.md`).
2. Add the storage (a column or a detail table) in `listings` with a migration, expose it in `selectors.view_of` as a flat
   registry id (add it to `selectors.view_of`, `validation.EDITABLE_FIELDS` and the `apply_updates` storage mapping), and
   accept it through the revision-checked edit service `apply_updates` (Story 2.2).
3. Serializers take enums from `catalog.registry`; never hand-copy values. New audit metadata is registered in
   `listings.apps` with enumerations only.


## Images (Stories 2.9 and 2.10)

`services.upload_image(actor, listing_id, data) -> ImageView`, `services.remove_image(actor, listing_id, image_id)` and
`services.set_image_order(actor, listing_id, base_revision, order, cover_id=None) -> ListingView`.

### Wire (AD-24)

`ListingView.media` (and the Listing resource's `media`) is a list ordered by `position` of `ImageView`:
`{id, position, is_cover, content_type, width, height, bytes, url}`. `url` is the relative path of the media endpoint,
`/api/v1/listings/{id}/images/{image_id}/file`. The owner id, the asset id and the storage key never appear. `media` is
`[]` for a Draft with none, and every Listing response (read, list, edit, 409 `current`, reorder) carries it. The
`ImageView` returned by an upload has the same shape. Saving other fields never touches images.

### Cover and order rules

Under the Listing row lock: the first image uploaded becomes the cover (position 0); a later upload is appended at
position `n` and never changes the cover; removing the cover promotes the image with the lowest remaining position (none
left: no cover); every removal renumbers the rest 0..n-1. A Listing with images always has exactly one cover.

### Refusals of `upload_image` and `remove_image`

In order: Visitor `unauthenticated`; a Listing the actor's account does not own (another
owner, an administrator, the system, an unknown or malformed id) `not_found`, identical for all of them; no
`edit_drafts` capability `forbidden` (evaluated after the not-found rule); a status outside
`lifecycle.EDITABLE_STATUSES` `forbidden`; 10 images already `too_many_images` (409). Then the upload is processed
OUTSIDE any lock (`kernel.images`: `ImageRejected` with a reason, or `Busy`), and in ONE transaction that locks the
account (the state is decided again from the locked snapshot), the Listing row and the storage usage row: the image
count is checked again (`too_many_images`), the cap is enforced (`storage_cap_exceeded`), the file and asset row are
written (the asset's `attached_at` is set in the same transaction), the `ListingImage` is inserted at the end, and the
Listing `revision` rises by one with a `ListingRevision` whose `changes` is `{"images": <count>}` (no file data). If the
transaction fails after the file was written, the file is removed. Uploads carry no `base_revision` (no stale
detection), but because the revision moves, a concurrent editor holding the old revision gets `409 stale_revision` and
reloads.

`remove_image` has the same refusals; an unknown image, another Listing's image and a repeat are `not_found`. In one
transaction the `ListingImage` and its asset are deleted (the storage usage is given back once), the rest is renumbered
(and the cover promoted if needed), the revision rises by one and the file is deleted after commit. Removing a Listing's
images when the Listing is deleted is Story 2.17.

### Reorder and cover: `set_image_order`

`order` is the full list of the Listing's image ids in the wanted order; `cover_id` is optional (`null` or omitted keeps
the current cover). Refusals, in order: Visitor `unauthenticated`; not owned `not_found`; no `edit_drafts` `forbidden`
(all before the body is looked at); a bad shape (`base_revision` not a positive integer: `required` or `invalid`;
`order` not a list of canonical UUIDs: `required` or `invalid`; `cover_id` not a UUID: `invalid`) `validation_error`.
Then, in one transaction that locks the account and the Listing row and works like `apply_updates`: a
`base_revision` that is not the stored revision raises `StaleRevision` carrying the current `ListingView` (nothing
written; checked before the order is judged and before the status); a status outside `EDITABLE_STATUSES` is `forbidden`;
an `order` that is not exactly the current set (a missing, duplicate, unknown or another Listing's id) is `invalid` on
`order`, a `cover_id` that is not one of the Listing's images `invalid` on `cover_id` (reported together). A request that
changes neither the order nor the cover returns the current view with no revision bump and no `ListingRevision` row.
Otherwise positions become 0..n-1, exactly one image is the cover, `revision` rises by one and one `ListingRevision`
records `changes = {"images": n, "order": true|false, "cover": true|false}` (counts and flags, never ids). Files, assets
and the storage usage are untouched. Two reorders with the same base: exactly one wins, the other gets `StaleRevision`.

### Endpoints

- `POST /api/v1/listings/{id}/images` (signed in, CSRF, `no-store`, `multipart/form-data` with exactly one
  `file` part, a 6 MB request cap, `201` with the `ImageView`; the `image_upload` rate limit applies).
- `PATCH /api/v1/listings/{id}/images` (signed in, CSRF, `no-store`, JSON `{base_revision, order, cover_id?}`, any other
  key `unknown_field`; not rate limited as an upload): `200` the Listing resource; `409 stale_revision` with the current
  Listing under `current` (the `ListingConflict` schema of the field PATCH); `400` `validation_error` with
  `fields.order`, `fields.cover_id` or `fields.base_revision` (codes only, never the submitted values); `401`; `403`
  (capability, status or CSRF); `404` (non-owners, administrators, unknown or malformed ids, whatever the body).
- `DELETE /api/v1/listings/{id}/images/{image_id}` (`204`).
- `GET|HEAD /api/v1/listings/{id}/images/{image_id}/file`: the media endpoint (see `docs/media.md`): the stored bytes to
  the Draft's owner, an identical `404` to everyone else. The seam is `selectors.image_for_serving`.

Upload errors: `400 validation_error` with `fields.file` one of
`too_large`, `unsupported_type`, `too_large_dimensions`, `too_small`, `invalid_image`, `required`, `invalid` (or
`non_field_errors: unknown_field` for any other part); `401`; `403` (capability or status; CSRF); `404`; `409`
`too_many_images` or `storage_cap_exceeded`; `415` for a non-multipart body; `429` with `Retry-After`; `503 busy` with
`Retry-After`. Details and the pipeline limits are in `docs/media.md`.

## Preview, delete and idle Drafts (Story 2.13)

### Preview

`selectors.preview_of(actor, listing_id) -> PublicListingView` is what the public will see of the owner's own `draft` or
`published` Listing. It calls the same projection function as the public read (`public_view_of`, so `public_fields` and the
registry class rule are the only field list; the private address is not even read) and skips only the `is_public` gate,
because the viewer is the owner (`policy.is_owner`: Active, Email-unverified and Suspended owners; not Deactivated). Another
account, an administrator, a Visitor, an unknown or malformed id are all `not_found`. The route adds `estimate`, always
`null` until the valuation feature exists, and is serialised by `ListingPreviewSerializer` (the public serializer built from
the registry's public fields). Image `url`s are the one media route: it serves a Draft's image to its owner and a public
Listing's to anyone (`docs/visibility.md`).

### Opening a Draft

`services.record_open(actor, listing_id, now=None)` sets `PropertyListing.last_opened_at` when the owner reads the Draft
(`GET /api/v1/listings/{id}`) or previews it, at most once an hour (when it is missing or older than an hour). It is one
`UPDATE`: it never changes `revision`, `updated_at` or the revision rows, and does nothing for a published Listing or for
anyone but the owner. The three retention columns (`last_opened_at`, `expiry_notice_sent_at`, `delete_after`) are never on
the wire.

### Delete

`services.delete_draft(actor, listing_id)`: refusals in order Visitor `unauthenticated`; not owned (another account, an
administrator, unknown or malformed id, a repeat) `not_found`; no `edit_drafts` (Suspended, Deactivated) `forbidden`; not a
`draft` `409 conflict` (a new catalogue code), nothing changed. In one transaction (lock the account, then the Listing row,
the order used everywhere) every image goes through `media.delete_asset(..., missing_ok=True)` (usage given back, files
deleted after commit, a failing file delete never fails the request, the orphan purge covers leftovers within 24 hours),
then the Listing row, which cascades to the terms, area, details, location, private address, amenities, image links and
revisions, and one audit row `listing.draft_deleted` (`target` the Listing, metadata `{"reason": "owner"}`, never content).
An edit, an upload or an image removal racing the delete ends in `404` or `409`, never an orphan (tests run two threads).
Deleting frees an open-Draft slot (the cap counts rows).

### Retention of idle Drafts

Two jobs on `run_retention` (`docs/runtime-and-retention.md`), registered from `ListingsConfig.ready`, code in
`listings.retention`; each picks candidate ids with `selectors` (at most 200 per run) and acts on each one in its own
savepoint through a system-actor service that decides again under the account and Listing locks. The activity time of a
Draft is `max(updated_at, last_opened_at, created_at)`.

- `listings.draft_notice(now, dry_run)`: a `draft` whose activity is **at or before** `now - 180 days`, with no notice yet
  and an owner who is not Suspended gets `expiry_notice_sent_at = now`, `delete_after = now + 30 days` and the in-app
  notification `listing.draft_expiring` (payload `{listing_id, days_left: 30}`, after commit, the owner only; the frontend
  renders a fixed sentence). Returns how many it noticed.
- `listings.draft_expiry(now, dry_run)`: a `draft` with `delete_after <= now` and an owner who is neither Suspended nor
  Deactivated. Decided again under the locks, in this order: activity later than the notice (opened or edited since)
  clears both columns and keeps the Draft (a later 180 idle days start again from that activity); no stored
  `listing.draft_expiring` notification for the Listing (`notifications.has_with_payload`: the notice may never have
  reached the owner) issues the notice again, `delete_after = now + 30 days`, nothing deleted; a deletion date more than
  2 days in the past (the owner was Suspended, or the job did not run) gives a fresh week (`delete_after = now + 7 days`,
  a new notice with `days_left: 7`), nothing deleted; otherwise it is deleted exactly like an owner delete (audit reason
  `retention`, actor kind `system`). Returns how many it deleted (not those cleared or extended).
- Suspended and Deactivated owners' Drafts are skipped by both jobs (selector and the locked re-check).
- Candidates are read in keyset pages by id (200 per page, at most 50 pages per run), so a Listing that always fails or is
  always skipped cannot starve the ones after it; anything left is picked up by the next run.
- `GET /listings/{id}` and the preview write `last_opened_at` at most hourly; the write is best effort (a failure is
  logged by exception class and never fails the read).
- `GET`-side notification kinds are not enumerated in `openapi.yaml` (the in-app list carries `kind` and `payload` as
  free JSON); the kind and payload of `listing.draft_expiring` are documented here and in `docs/notifications.md`.
- Postgres only: `GREATEST` ignores a NULL `last_opened_at`, which other databases do not do. The idle partial index
  covers `updated_at`; the `GREATEST` expression is not index-served (deferred, fine at MVP scale).
- A dry run changes and notifies nothing and returns the would-be counts; a second run the same day does nothing;
  published and every other non-draft Listing is never a candidate; one failing Listing is logged by exception class only
  and does not stop the others (its savepoint rolls back, so it is retried on the next run).
- Indexes: partial `listings_draft_idle_idx` (`updated_at`, drafts) and `listings_draft_expiry_idx` (`delete_after`, drafts
  with a notice); the constraint `listings_expiry_notice_paired` keeps the two notice columns both set or both null.

## Publication quota and allowance (Story 2.14, AD-7)

`listings.models.ListingQuotaPolicy` holds the monthly limit per account type (`individual`, `agency`; plain strings, the
identity enum is never imported), effective-dated: `effective_from` inclusive, `effective_to` exclusive or NULL. CHECKs
cover the type, a non-negative limit and `effective_to > effective_from`; a PostgreSQL exclusion constraint
(`listings_quota_no_overlap`, `btree_gist`) makes overlapping ranges of one type impossible. Migration `0013` creates the
extension and seeds individual 2 and agency 10 from 2000-01-01 UTC (idempotent: a type that has any row is left alone).
`btree_gist` is a trusted extension, so the migrator (database owner) can create it; `docker/postgres/create_roles.sh`
also creates it in the database and `template1` (like `vector`), so test databases have it. Policies are written only by
that migration and, later, Story 2.20's service.

`listings.PublicationEvent` is the history `listings.publication.publish` writes (Story 2.15; kinds `initial`, `republish_counted`,
`republish_free`, `restore`, `seed`; `counted` only for the first two, by CHECK; no foreign keys, so history outlives a
Listing; idempotency key unique per account). Never updated or deleted by application code, with ONE exception: `publication.remove_seed_events`
(kind `seed` only, system actor, privileged connection; the sample-data reset). The runtime role has
INSERT and SELECT only (`RUNTIME_APPEND_ONLY_TABLES`). Only `listings/publication.py` writes it (an AST guard in
`tests/listings/test_quota.py` lists the writers, and follows a name bound to the model, so a split `rows = PublicationEvent.objects...; rows.filter().delete()` is flagged too; tests may insert directly).

`listings.quota`: `quota_limit_at(account_type, at)` resolves the limit at the time of an attempt (never stored on an
event; `QuotaPolicyMissing` if no row covers `at`). `allowance_for(account_id, account_type, now)` returns a frozen
`Allowance(limit, used, remaining, period_start, resets_at)`: the month is the calendar month of `now` in `Asia/Karachi`
(fixed UTC+5), `used` counts the account's counted events in `[period_start, resets_at)`, `remaining = max(limit -
used, 0)`. `owner_allowance(actor, now)` adds the owner check (Visitor `unauthenticated`; administrator, system actor and,
for a direct service call, Deactivated `forbidden`; over HTTP a Deactivated account has no session, so the route answers
`401` before the service runs; Suspended and Email-unverified owners may read). The view passes the clock.
Response datetimes carry the `+05:00` offset. Enforcement and consumption: `publish`, below.

Notes for the next stories:

- `publish` reads the allowance (and inserts the event) inside one transaction that first calls
  `identity.selectors.lock_account(account_id)`, so two simultaneous publishes cannot both see the last slot. The
  allowance read itself is two queries (limit, then count) and takes no lock.
- The exclusion constraint is not deferrable. Story 2.20 closes the current row (`effective_to`) and inserts the new
  one in the same transaction, in that order.
- All instants are timezone-aware; `quota_limit_at`, `month_bounds` and `allowance_for` raise `ValueError` for a naive
  datetime. `ZoneInfo("Asia/Karachi")` needs tz data: `tzdata` is a runtime dependency on every platform.
- `QuotaPolicyMissing` is an internal error: over HTTP it is the generic `500 server_error` envelope without detail.

## Publication (Story 2.15, AD-7)

`listings.publication.publish(actor, listing_id, base_revision, idempotency_key, now=None)` is the only code that sets
`ListingStatus.PUBLISHED` and the only writer of `PublicationEvent` (guards: `tests/architecture/
test_publication_single_writer.py`, `tests/listings/test_quota.py`). `now` is the clock for tests. There is no
`set_status` path: nothing else changes a Listing's status.

One `transaction.atomic`, in this order: (1) claim the key (`kernel.idempotency`, scope `listings.publish`, per account,
request hash over listing id and `base_revision`); a replay inside 24 hours returns the Listing as it is now (`200`,
nothing else written) and the same key with another hash is `422 idempotency_key_reused`; (2) `lock_account`, then lock
the owned Listing row; (3) `base_revision` against `revision` (`409 stale_revision` + `current`); (4) status must be
`draft` (`409 conflict`), and the Listing must not be restricted (`403 forbidden`, Story 2.18: a restriction freezes a Listing whatever its status); (5) account state: only Active publishes (`403 email_not_verified`, `403
publishing_unavailable` for Suspended, `403 forbidden` for a Deactivated account called directly; a Visitor,
administrator, system actor, other owner or unknown id is `401`/`404` before the transaction); (6) validate (below);
(7) allowance: `quota.allowance_for` under the account lock with `now` (read after the lock wait unless the caller passes
it, so a request that crosses Karachi midnight while waiting counts in the new month); `remaining < 1` is `409 quota_exhausted` with the
caller's own `details: {limit, used, resets_at}` (`resets_at` is an offset-aware ISO string); (8) one counted `initial`
event with the identity fingerprint and the key; (9) status `published`, `revision + 1`, a `ListingRevision` row
(`changes: {"status": "published"}`), the Draft expiry markers cleared; (10) audit `listing.published` (target id only);
(11) complete the claim (`listing:<id>`); (12) emit `ListingPublished(listing_id, account_id, republished=False)` after commit (nothing
subscribes yet). Any refusal or exception rolls all of it back, the claim included, so nothing is consumed and the key
can be retried. A key older than 24 hours (or whose claim was purged) is looked up in the account's events: for the same Listing it
replays the original success and completes the claim again (a late retry still learns it worked); for another Listing it is
`idempotency_key_reused`. A malformed or dangling stored reference, or a Listing that is gone, replays as `not_found`; a
claim with no result is `conflict`. No AI call, upload, mail or notification runs inside.

Validation (`publication.problems_of`, all at once, `400 validation_error` keyed by registry id): the required list is
derived from the registry (`requirement: required_for_publication` plus applicability: purpose, category, subtype,
title, asking price, price basis, area value and unit, city and locality, plus rental period for a rent and bedrooms and
bathrooms for a House or Apartment), a missing item is `required`, and each stored value is judged again with the
current per-field rules (`validation.evaluate`, so a locality of another city is `locality_not_in_city`, an inactive
city `invalid`). No LIVE image (a link whose asset row exists, `selectors.live_image_count`, the rule of the image cap too) is `images: images_required` ("Add at least one image."). Every stored value that is present, optional ones included, is judged again with the cross-field rules (floor against floors, coordinate pairing, marla basis, locality against city), so stale invalid data blocks with a field-level error; a field that does not apply but holds a value is `not_applicable`. A value of a `quota_identity` or
`private` field that currently has a value and whose provenance is `origin: ai` with `confirmed` anything but `true` (missing included) is `confirm_required` ("Confirm this value before
publishing."), keyed by that field; the owner confirms by saving the value. Optional data never blocks. The `Idempotency-Key` header (1 to 128 characters of
`A-Za-z0-9._:-`) and `base_revision` problems are `400` under `idempotency_key` and `base_revision`.

Fingerprint: `publication.identity_fingerprint(view)` is the SHA-256 hex of the canonical JSON (sorted keys, no
whitespace, ASCII, decimals normalised, text in NFC) of the registry fields flagged `quota_identity` (since Story 2.17, registry version 9: purpose,
category, subtype, city, locality, area_value, area_unit and marla_basis; the same flag locks them while Published). It is
stored on the event and never leaves the service. Version 9 changes the fingerprint, so migration
`0015_identity_fingerprint_v9` recomputes `identity_fingerprint` of every event (`initial`, `seed`, later kinds) whose
Listing still exists, from the stored Listing with the canonicalisation frozen inside the migration (no live imports),
without consuming quota or writing events; it is idempotent and its reverse is a no-op. Events of deleted Listings keep
their value. Since Story 2.18 the loop is `identity_migration.recompute_fingerprints` (below, the drift rule). The widening of the identity set also widens `confirm_required` (an AI-written, unconfirmed value of
city, locality, area value, area unit or marla basis blocks publication until the owner confirms it): this is intended.

Error codes added (Story 2.15; Story 2.18 adds none): `quota_exhausted` (409), `email_not_verified` (403), `publishing_unavailable` (403). Field
sentences added: `images_required`, `confirm_required`.

A `listing.draft_expiring` notice already delivered is not withdrawn when the Draft is published. It links to the Draft
page and only says the Draft will be deleted if it is not opened, so a published Listing is never deleted by it (the
retention job only considers Drafts, and publication clears `delete_after`).

## Republication (Story 2.18, AD-7)

`listings.publication.republish(actor, listing_id, base_revision, idempotency_key, now=None)` takes an **Archived**
Listing back to Published. It returns `Republication(listing, kind)` (`kind` is `republish_counted` or `republish_free`).
`publish` and `republish` share ONE transaction, `publication._run`, parameterised by a `_Mode` (idempotency scope, the
status the Listing must have, the event kinds a key of this mode may have produced, the audit action); nothing is
copied. The guard (`tests/architecture/test_publication_single_writer.py`) pins that `publication.py` writes only the
published status and only `publish` and `republish` call `_run`.

**Order of refusals** (pinned by `test_republication.py`): shape checks, then inside the transaction stale revision, status,
restricted, account state, validation, quota (so an incomplete Listing with a used-up allowance is a `400`, a restricted
Listing of an Email-unverified owner a `403 forbidden`). Same guarantees as `publish`: shape checks before the transaction (Visitor `401`; another owner, an administrator,
a system actor, an unknown or malformed id `404`; a bad `base_revision` or key `400`), then inside one transaction: claim
(scope `listings.republish`, hash over listing id and `base_revision`; a replay returns the original result, the original
`publication_kind` included, with no second charge, event or audit record; the same key with other content is `422
idempotency_key_reused`; an expired or purged claim replays from the event exactly as for `publish`), `lock_account` then
the Listing row, the clock is read after the lock, `base_revision` (`409 stale_revision` + `current`), the status must be
`archived` (`409 conflict` for a Draft, Published, **Sold or Rented** Listing, which must first be returned to Archived
with `change_status`, and for a Removed one, which never republishes), a **restricted** Listing is `403 forbidden` (the
restriction stays in force), account state (only Active: `403 email_not_verified`, `403 publishing_unavailable`, `403
forbidden` for Deactivated called directly), then the FULL re-judge of the whole Listing through the same completeness
core as `publish` (`completeness.problems_of` without the `touched` set: every required item, every stored value with the
current rules, city and locality, at least one live image, unconfirmed Assistant values of private or identity fields),
all problems at once as `400 validation_error`; then the counted or free decision; the event; status `published` and
`revision + 1` with a `ListingRevision` row (`changes: {"status": "published"}`); audit `listing.republished` (the Listing
id is the target; metadata `{"kind": <event kind>}` only); the claim result; `ListingPublished` after commit (the SAME
event as `publish`, with `republished=True` so a future consumer can tell them apart; `false` for a publication). Any refusal
or failure rolls all of it back: no quota, event, audit record, claim or status change. Images, revision history and
everything else on the Listing are untouched.

**Counted or free (the rule).** Compute `identity_fingerprint` of the Listing as it is now. The baseline is the Listing's
last COUNTED event (`initial` or `republish_counted`; `seed` and `republish_free` events are never a baseline; events of
the Listing only, latest `created_at`). The event is `republish_free` (`counted=false`, no allowance needed or used) only if
ALL hold: a baseline exists, its `created_at` falls in the same **Karachi** calendar month as `now`
(`quota.month_bounds(now)`, half-open; UTC and Karachi differ by five hours, so 18:59:59 UTC on the 30th is still that
month and 19:00:00 UTC is the next), and is not later than `now` (a baseline in the future of the clock is not in the past, so it never makes a free
republication), and its stored fingerprint equals the current one (a NULL one never does). Otherwise it is
`republish_counted` (no baseline, another month, a baseline later than `now`, a NULL fingerprint, or a changed identity field). A counted republication
needs `allowance.remaining >= 1` under the account lock (else `409 quota_exhausted`, same `details` as publishing,
nothing consumed); a free one never reads the allowance, so an exhausted account or a limit of 0 can still republish free.
A counted event becomes the new baseline; a free one never moves it. `identity_fingerprint` is always stored. The eight
identity fields are the registry flag `quota_identity` (purpose, category, subtype, city, locality, area value, area unit,
marla basis); the owner may edit them while Archived (2.17), which is exactly what makes the next republication counted (changing a field and back is counted both times, because each counted
republication moves the baseline).
`quota.allowance_for` counts `republish_counted` events and ignores free ones. A key belongs to one operation: a key that
produced a publication cannot republish (`422 idempotency_key_reused`) and the other way round (the unique
`(account, key)` index is shared by every event kind).

**Endpoint.** `POST /api/v1/listings/{id}/republish`, body `{base_revision}`, header `Idempotency-Key` (validated as for
publish), CSRF, owner only, `Cache-Control: no-store`. `200` returns the owner Listing plus `publication_kind`
(`RepublishedListing` in the OpenAPI; the publish response is unchanged). `400`, `401`, `403`, `404`, `409` (`stale_revision`
with `current`, `conflict`, `quota_exhausted` with `details`), `422`. No new error code and no new field sentence.

**Changing the identity set (the procedure).** A registry change that adds or removes a `quota_identity` field changes
`identity_fingerprint` for every Listing, so stored baselines would no longer match and every republication would be
counted. A story that changes the set MUST, in the same change: (1) bump the registry version; (2) add a data migration
modelled on `0015_identity_fingerprint_v9`: it keeps the OLD and NEW identity tuples and its own FROZEN canonicalisation
(no import of `publication` or `registry`: decimal fields, NFC-trimmed text, integers as numbers, sorted-key compact ASCII
JSON, SHA-256) and calls `listings.identity_migration.recompute_fingerprints(apps, old, new, canonical, load)`; (3) test it.

The drift rule (never overwrite baseline evidence with data that drifted): an event's fingerprint is recomputed only
when the stored value equals the OLD-set fingerprint of the Listing's CURRENT data, which proves the data is what the event
recorded. When it does not (typically an Archived Listing whose identity was edited after the event) it is set to NULL, and a
NULL baseline never matches, so the next republication is COUNTED: conservative, it can cost the owner a publication but
never let them dodge the quota. A value already equal to the NEW fingerprint is kept (a second run changes nothing).
Events of deleted Listings are left alone. Only the fingerprint column changes: no quota consumed, no event, claim or audit
record written. Tests that pin each claim: `test_the_migration_through_the_executor_recomputes_unchanged_nulls_drifted_and_keeps_the_rest`
(`MigrationExecutor` back to 0014 and forward again: unchanged recomputed to the live value, seed and free events alike,
drifted NULL then counted, already-new kept, deleted left alone, ledger identical, idempotent, reverse a no-op) and
`test_a_new_identity_field_recomputes_unchanged_listings_and_nulls_drifted_ones` (a simulated new field, `bedrooms`, through
the same function: the frozen result equals the live fingerprint under the grown registry) in
`tests/listings/test_republication.py`; `tests/listings/test_lifecycle_review.py` covers the version-9 migration itself
and the frozen canonicalisation against the live one for every field shape.

### The `seed` kind (Story 2.16)

`publish(..., kind="seed")` is keyword-only (default `initial`; any other value is `validation_error`). ONLY the system
actor may pass `seed` (any other actor, a user included, is `403 forbidden` with nothing written; a Visitor is
`unauthenticated`); the HTTP view never passes `kind`, so no route can produce one (`tests/listings/test_sample_data.py`
checks the call sites and an injected body field). The system actor publishes the Draft of a SAMPLE account (an address on
`sample.estate-ai.invalid`); any other owner is `not_found`. The event is written with `kind=seed`, `counted=False`:
no allowance is needed (an account with a used-up month can still be seeded) and none is used. Everything else is as
above: the idempotency claim (scoped to the owner), account then Listing locks, `base_revision`, the account-state
refusals, complete validation, the audit record and `listing.published` after commit.

`publication.remove_seed_events(actor, account_ids, using=...)` is the one place an event is deleted: system actor only,
`kind=seed` and `counted=False` rows of the given accounts only. The runtime role may only INSERT and SELECT events, so
the sample-data command passes the privileged connection alias (`sample_data.privileged_alias()`: `migrator` where the
settings define it, i.e. local development, otherwise `default`, which is the schema owner under the test settings).
Decision: seed events ARE removed on `--reset` (rather than left as harmless history) so a reset leaves no trace of the
accounts that went away and a reseed never meets an old key; a seed idempotency key is `seed-<index>` per owner and a
reseed creates new accounts, so keys cannot clash either way. See `docs/sample-data.md`.
