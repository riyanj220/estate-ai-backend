# Field registry

The Field registry (AD-4) is the one definition of which Listing fields exist. It lives in `catalog`:

- `apps/catalog/registry/fields.yaml`, the definition, found relative to the module so it works from any working directory
  (the repo is run from source, not packaged; PyYAML is a runtime dependency);
- `apps/catalog/registry/fields.lock`, the version and content hash;
- `apps/catalog/registry.py`, the loader and the accessors (`registry_version()`, `field(id)`, `fields()`, `categories()`,
  `purposes()`, `subtypes_of(category)`, `valid_subtype(category, subtype)`), which return frozen dataclasses.

## File format

```yaml
version: 2                     # positive integer
taxonomy:
  purposes: [sale, rent]       # code-defined, snake_case ids
  categories:
    - {id: plot, subtypes: [residential_plot, commercial_plot, plot_file]}
fields:
  - id: purpose                # stable snake_case id
    type: enum                 # enum | text | money | integer | decimal | boolean
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
  - id: price_basis
    type: enum
    allowed_values: [total, per_unit, rental_period]
    allowed_values_by_purpose:   # optional, enums only: every purpose, each a non-empty subset
      sale: [total, per_unit]
      rent: [rental_period]
```

Field types: `enum` (`allowed_values`), `text` (`max_length` 1 to 20 000, optional `min_length`; lengths count
characters after normalisation), `money` (`min`, `max` as quoted decimal strings, never YAML numbers, at most two
decimals and at most 999 999 999 999.99, `currency` must be `PKR`; the column is NUMERIC(14,2)), and the declared but
not yet editable `integer`, `decimal`, `boolean`. A length key on a non-text field, a money key on a non-money field and
`allowed_values_by_purpose` on a non-enum are refused. `applicability` decides whether a field applies to a Listing
(`rental_period` applies only to purpose `rent`); `FieldDef.applies_to(purpose, category, subtype)` and
`FieldDef.values_for(purpose)` answer it, and `listings.validation` enforces both from the registry.

`requirement` states when a value is needed, not when it may be saved: `required_for_publication` fields (and cross-field
completeness such as a rental priced per rental period naming its period) are checked by publication (Story 2.15);
`listings.services.apply_updates` accepts partial data.

Version 2 (Story 2.2) adds `title`, `description`, `asking_price`, `price_basis` and `rental_period`. `asking_price` is
not a `valuation_input`; no shipped field is yet, and the `listing.model_inputs_changed` behaviour is proven with a
registry fixture. `title`, `description` and `asking_price` are `ai_extractable`; the price basis and rental period are
not (the owner states them).

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
unless narrowed), min_length and max_length (text only), min, max and currency (money only), class` and the five flags
(`RESPONSE_SHAPE_VERSION` is 2).
`Cache-Control: public, max-age=300` and a strong `ETag` (`"<version>-<content hash>"`); `If-None-Match` gets `304`.
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
  generated CHECKs on `title`, `description` and `ListingTerms` (price range, basis and period values): changing those
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
