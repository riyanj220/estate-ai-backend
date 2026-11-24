# Field registry

The Field registry (AD-4) is the one definition of which Listing fields exist. It lives in `catalog`:

- `apps/catalog/registry/fields.yaml`, the definition, found relative to the module so it works from any working directory
  (the repo is run from source, not packaged; PyYAML is a runtime dependency);
- `apps/catalog/registry/fields.lock`, the version and content hash;
- `apps/catalog/registry.py`, the loader and the accessors (`registry_version()`, `field(id)`, `fields()`, `categories()`,
  `purposes()`, `subtypes_of(category)`, `valid_subtype(category, subtype)`), which return frozen dataclasses.

## File format

```yaml
version: 5                     # positive integer
taxonomy:
  purposes: [sale, rent]       # code-defined, snake_case ids
  categories:
    - {id: plot, subtypes: [residential_plot, commercial_plot, plot_file]}
fields:
  - id: purpose                # stable snake_case id
    type: enum                 # enum | text | money | decimal | integer | boolean
    requirement: required_for_publication   # | recommended | optional
    applicability: {purposes: [], categories: [], subtypes: []}   # empty = all
    allowed_values: [sale, rent]   # enums only; purpose, category and subtype must equal the taxonomy
    class: public              # public | private
    quota_identity: true       # flags, all default false: searchable, sortable, ai_extractable,
                               # valuation_input, quota_identity
  - id: title
    type: text                 # text: max_length (required), min_length (default 0)
    max_length: 100
    min_length: 10
    ...
  - id: asking_price
    type: money                # money: min, max (decimal strings, at most 2 decimals), currency PKR
    min: "0.01"
    max: "999999999999.99"
    currency: PKR
    ...
  - id: area_value
    type: decimal              # decimal: min, max (decimal strings, at most `scale` decimals), scale (1 to 8)
    min: "0.0001"
    max: "9999999999.9999"
    scale: 4
    ...
  - id: bedrooms
    type: integer              # integer: min, max (YAML integers, 0 to 1 000 000 000, min <= max)
    min: 0
    max: 50
    ...
  - id: lift
    type: boolean              # boolean: no extra keys; the answer is true, false or unknown (null)
    ...
  - id: area_sqft
    type: decimal
    derived: true              # computed by the service; never input (optional, not ai_extractable or a model input)
    description: Area in square feet, computed from ...   # optional short text, 1 to 200 characters
    ...
  - id: price_basis
    type: enum
    allowed_values: [total, per_unit, rental_period]
    allowed_values_by_purpose:   # optional, enums only: every purpose, each a non-empty subset
      sale: [total, per_unit]
      rent: [rental_period]
  - id: possession_status
    type: enum
    allowed_values: [possession_ready, possession_pending, file_only]
    allowed_values_by_subtype:   # optional, enums only: one entry per Subtype the field applies to, each a non-empty subset
      residential_plot: [possession_ready, possession_pending]
      commercial_plot: [possession_ready, possession_pending]
      plot_file: [file_only]
    seller_declared: true        # optional boolean, default false: an owner's claim, never platform-verified
  - id: covered_area_sqft
    type: decimal
    error_code: invalid_area     # optional, decimals only: invalid_measure (default) or invalid_area
```

Field types: `enum` (`allowed_values`), `text` (`max_length` 1 to 20 000, optional `min_length`; lengths count
characters after normalisation), `money` (`min`, `max` as quoted decimal strings, never YAML numbers, at most two
decimals and at most 999 999 999 999.99, `currency` must be `PKR`; the column is NUMERIC(14,2)), `decimal` (`min`, `max`
as quoted decimal strings with at most `scale` decimals and at most 16 whole digits, `scale` from 1 to 8; money stays its
own type), `integer` (`min`, `max` as YAML integers from 0 to 1 000 000 000, `min` at most `max`; no scale or currency; the column is a 32-bit integer) and `boolean` (tri-state: true, false, or unknown as null; no extra keys). An enum value may carry a decimal part (`"272.25"`) only when every value of that enum is a number that fits
NUMERIC(6,2) (up to 4 whole digits and 2 decimals) and no two are numerically equal (`225` and `225.00`).
`derived: true` marks a field the service computes from others: it must be `optional` and not `ai_extractable`,
`valuation_input` or `quota_identity`, it is rejected on input as `not_editable`. The loader enforces only the derived field's own rules (the flag and its
restrictions); it cannot see `listings`, so that the derived fields are exactly those in `listings.fields.DERIVED` and
never in `STORAGE` or `EDITABLE_FIELDS` is enforced by `tests/listings/test_fields.py`.
An optional `description` documents what a value means (served to forms). A length key on a non-text field, a money key on a field that is neither money nor decimal, `scale` on a non-decimal field,
`currency` on a decimal field and
`allowed_values_by_purpose` on a non-enum are refused. `applicability` decides whether a field applies to a Listing
(`rental_period` applies only to purpose `rent`); `FieldDef.applies_to(purpose, category, subtype)` and
`FieldDef.values_for(purpose, subtype=None)` answer it, and `listings.validation` enforces both from the registry.

`requirement` states when a value is needed, not when it may be saved: `required_for_publication` fields (and cross-field
completeness such as a rental priced per rental period naming its period) are checked by publication (Story 2.15);
`listings.services.apply_updates` accepts partial data.

Version 2 (Story 2.2) adds `title`, `description`, `asking_price`, `price_basis` and `rental_period`. `asking_price` is
not a `valuation_input`. (In version 2 no field was; since version 3 the area inputs are, see below, and the
`listing.model_inputs_changed` behaviour is proven both with the area fields and with a registry fixture.) `title`, `description` and `asking_price` are `ai_extractable`; the price basis and rental period are
not (the owner states them).

Version 3 (Story 2.3) adds the `decimal` type, `derived`, `description` and the area fields `area_value` (decimal,
scale 4, `0.0001` to `9999999999.9999`; `searchable`, `sortable`, `ai_extractable`, `valuation_input`),
`area_unit` (`sqft`, `sqyd`, `marla`, `kanal`, `acre`; `searchable`, `ai_extractable`, `valuation_input`), `marla_basis`
(`"225"`, `"250"`, `"272.25"`: square feet in one marla; applies only while the resulting unit is marla or kanal, which is
code-defined in `listings.area`; `ai_extractable`, `valuation_input`) and the derived `area_sqft` (decimal, scale 4,
`searchable`, `sortable`, not a valuation input). `area_value` and `area_unit` are `required_for_publication`; the
others are `optional`; all apply to every Category and Subtype. See `docs/listings.md` for the conversion rules.

Version 4 (Story 2.4) adds the `integer` and `boolean` types and the House and Apartment detail fields from
`bedrooms` to `maintenance_charges` (30 fields, applicability by Subtype; see `docs/listings.md`). Every one is class
public and `ai_extractable`; `bedrooms`, `bathrooms`, `construction_year`, `condition` and `covered_area_sqft` are
`valuation_input`, and `bedrooms`, `bathrooms`, `furnishing` and `parking_spaces` are `searchable`.
`construction_year` has one requirement level in the registry (`recommended`) for both Subtypes. Integer bounds are
served in `min` and `max` as decimal strings (`"0"`, `"50"`), like money and decimal bounds.

Version 5 (Story 2.5) adds two registry features and the Plot and Commercial catalogue (see `docs/listings.md`):

- `seller_declared: true|false` (default false, must be a boolean) marks a legal, approval, possession or similar
  claim the owner makes. It is served in the registry payload. **The rule:** a seller-declared attribute is shown with
  a "Seller-declared" label and is never given a platform badge, check mark or "verified" wording; any later search or
  display layer must read this flag rather than a hand-kept list. It does not change validation.
- `allowed_values_by_subtype` (enums only) narrows `allowed_values` per Subtype, so vocabulary rules live in data
  (a Plot File allows only `file_only` for `possession_status`). The loader requires one entry for every Subtype the
  field applies to and no other (`applicability.subtypes`; else the subtypes of the listed `categories`; else all
  Subtypes), each a non-empty subset of `allowed_values` without duplicates. A value that no Subtype offers is refused,
  and with `allowed_values_by_purpose` every Subtype subset must share a value with every purpose subset.
  `values_for(purpose)` without a Subtype on such a field returns `()` (nothing is allowed without the Subtype). It may be combined with `allowed_values_by_purpose`: a value must pass
  both. Validation takes the Subtype from the Draft's identity; a value outside it is `invalid_choice`. The payload
  serves the map (`allowed_values_by_subtype`, null when absent) and `RESPONSE_SHAPE_VERSION` is 4 because the two
  keys were added.

`error_code` (decimals only, `invalid_measure` default, `invalid_area` for `area_value` and `covered_area_sqft`) names the
code for a well-formed value outside the field's rules; the validator reads it from the registry. `invalid_measure` is
unit-neutral text. The five `seller_declared` fields are not `ai_extractable`: legal and possession claims are never
extracted by the Assistant.

**Known limitation:** a field has one requirement level, there are no per-Subtype levels yet, so `water` and
`building_floors` are `recommended` for commercial Subtypes too (the story text wanted optional or per-Subtype levels).

Version 5 also widens `applicability.subtypes` of ten shared unit fields to Shops and Offices (same ids and columns),
adds the utilities `electricity`, `water`, `gas`, 14 plot fields (five `seller_declared`) and 8 commercial fields. The
registry lets a field have one requirement level only. New field ids are always added at the END of the file, because
the wire order of a Listing's `fields` follows the registry file order (see `docs/listings.md`).

Only these keys are allowed. A Subtype belongs to exactly one Category; Plot File is a Subtype of Plot, distinct from
Residential Plot.

## Rules enforced at start-up

`CatalogConfig.ready` loads the file; any problem raises `ImproperlyConfigured` with a one-line reason and the
backend does not start: unknown keys, a repeated key, duplicate field or category ids, a Subtype under two Categories,
an enum whose values differ from the taxonomy, a missing or non-integer version, an unknown type, requirement or class,
applicability naming values outside the taxonomy, and the hard rule that a **private-class field flagged `searchable`,
`sortable` or `valuation_input` is rejected**. `tests/catalog/test_registry.py` has a bad fixture per rule.

## Versioning and the lock

Every Listing stores the `registry_version` it was written under. To change the registry:

1. edit `fields.yaml` and raise `version`;
2. run `uv run python scripts/registry_lock.py` (it refuses a changed file whose version did not rise) and commit both files.

The lock holds the version and the SHA-256 of the canonical content (the parsed YAML serialised with sorted keys, so
comments and layout do not count). `test_lock_matches_the_registry` fails when the YAML changed without the lock and
tells the developer to bump the version and run the script.

## Serving

`GET /api/v1/registry` is public: `{version, taxonomy: {categories: [{id, subtypes}], purposes}, fields: [...]}`, each
field with `id, type, requirement, applicability, allowed_values (null unless an enum), allowed_values_by_purpose (null
unless narrowed), allowed_values_by_subtype (null unless narrowed), seller_declared, min_length and max_length (text only), min and max (money, decimal and integer, plain decimal notation, never an exponent), currency (money only), scale (decimal only),
derived, description (null unless documented), class` and the five flags (`RESPONSE_SHAPE_VERSION` is 4: `allowed_values_by_subtype` and `seller_declared` were added in version 5; earlier it
rose for `scale`, `derived` and `description`).
`Cache-Control: public, max-age=300` and a strong `ETag` (`"<version>-<content hash>-<RESPONSE_SHAPE_VERSION>"`); `If-None-Match` gets `304`.
OpenAPI enums for purpose, category and subtype are generated from the registry (`PurposeEnum`, `CategoryEnum`,
`SubtypeEnum`), never copied by hand. Cities, localities and amenities are database reference data, not registry values.

## Rules and caveats added in review

- **Id lengths.** Every purpose, category and subtype id must be at most 32 characters (the Listing columns are
  `CharField(max_length=32)`); the loader rejects longer ones. Enum `allowed_values` use a looser pattern (lowercase
  letters, digits, underscore; may start with a digit) than the snake_case ids of fields and taxonomy entries.
- **Hardening.** Mapping keys must be plain strings and YAML merge keys (`<<`) are refused. A field cannot list a
  subtype whose category is missing from a non-empty `categories` list.
- **Review the lock with the YAML.** Reviewers must inspect any diff of `fields.yaml` together with `fields.lock`. The
  lock test is advisory: a hand-edited lock must come with a version increase (`scripts/registry_lock.py` refuses a lower
  version, and a changed file at an unchanged version).
- **The taxonomy is also in the database.** Purpose, category and subtype are frozen into the CHECK constraints of
  `listings_propertylisting`, so a taxonomy change needs a `listings` migration as well as a version bump
  (`tests/listings/test_constraints.py` fails when the migration and the registry disagree). The same holds for the
  generated CHECKs on `title`, `description`, `ListingTerms` (price range, basis and period values) and `ListingArea`
  (value and square-foot bounds, unit and basis values, the marla and kanal units a basis applies to) and
  `ListingResidential`, `ListingPlot` and `ListingCommercial` (integer, decimal and money bounds, enum sets, the floor rule): changing those
  registry bounds or values needs a migration.
- **Private fields are listed.** Private-class fields appear in the public registry response by design: it is field
  metadata for owner forms, never values. `ai_extractable` and `quota_identity` are allowed on private fields; only
  `searchable`, `sortable` and `valuation_input` are refused.
- **ETag.** The tag is `"<version>-<content hash>-<RESPONSE_SHAPE_VERSION>"`; bump `RESPONSE_SHAPE_VERSION` in
  `apps/presentation/api/registry.py` when the serializer changes without a registry change.
- **Open-Draft cap policy.** A `limit` of 0 would block all Draft creation (the table's check currently requires
  `limit > 0`); a missing `open_drafts` row is a configuration error (`ImproperlyConfigured`, a 500).
- **Bounds are frozen into migrations.** Tightening or loosening any text length, money bound or enum value changes
  generated CHECK constraints, so it needs a data-safe migration (check existing rows first: a tighter bound can
  fail on stored data) plus a version bump. `tests/listings/test_constraints.py` fails when migration 0002 and the
  constraints generated from the current registry differ, and has a test proving a changed bound is detected.
