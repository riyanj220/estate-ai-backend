---
title: 'Story 2.6: Sale and rent terms (backend half, then frontend half)'
type: 'feature'
created: '2026-11-24'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-5-plot-and-commercial-details.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-4-house-and-apartment-details.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-2-conflict-safe-editing.md'
  - '{project-root}/_bmad-output/planning-artifacts/prds/prd-estate-ai-backend-2026-11-18/addendum.md (section 8: Sale terms and Rent terms rows)'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/registry.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Draft carries its Asking price, price basis and rental period (Story 2.2) but not the rest of the commercial terms buyers and renters expect (negotiable price, possession availability, deposit, availability date, lease terms, utilities, pets, occupant rules), and deferred terms such as installments must stay unsupported.

**Approach:** Add the sale and rent terms as registry fields with applicability by purpose, stored as typed nullable columns on the existing `ListingTerms` row (lazily created as today), saved through the existing `apply_updates` and `PATCH`. One new registry type `date` is added (ISO calendar dates). The frontend half needs only labels, a date control and a "Terms" grouping in the generic Details section. Backend first and merged, then the frontend in `/home/riyan/Riyan/projects/estate-ai-frontend`.

## Boundaries & Constraints

**Always (backend):**
- **Registry version 6** (bump, lock; run `scripts/registry_lock.py` properly this time). New type `date`: JSON string `YYYY-MM-DD` only (ASCII digits, a real calendar date, between `2000-01-01` and `2100-12-31`; anything else `invalid`, out of window `invalid_date`), stored as a SQL DATE, served as `YYYY-MM-DD`, never a timestamp; the loader accepts `min`/`max` date strings for the type and the serializer/OpenAPI document it. All new fields are class public, `ai_extractable` true except where stated, no `valuation_input` unless stated, applicability by `purposes`.
  - **Both purposes:** `price_negotiable` (boolean, optional).
  - **Sale only (`purposes: [sale]`):** `possession_availability` (enum `available_now`, `within_one_month`, `within_three_months`, `after_three_months`; recommended; `seller_declared: true`; `ai_extractable: false`).
  - **Rent only (`purposes: [rent]`):** `security_deposit` (money, min "0", max "999999999999.99", recommended), `availability_date` (date, recommended), `advance_rent` (money, same bounds, optional), `minimum_lease_months` (integer 1 to 120, optional), `notice_period_days` (integer 0 to 365, optional), `utility_electricity_included`, `utility_gas_included`, `utility_water_included`, `utility_internet_included` (booleans, optional), `pets_allowed` (boolean, optional), `occupant_restrictions` (enum `no_restriction`, `families_only`, `bachelors_only`; optional; `seller_declared: false`). Existing fields keep their meaning: for rent the `asking_price` is the rental amount (required) and `rental_period` is required and never assumed; `furnishing` and `maintenance_charges` stay as already defined (their Subtype applicability is unchanged; document that maintenance charges for rent houses remain a later registry addition).
- **Storage**: typed nullable columns on `ListingTerms` (one per new field; DATE column for `availability_date`; NUMERIC(14,2) for money; small integers; nullable booleans), registry-generated CHECKs frozen in migration `0006_terms` with drift tests; unanswered is `null`, never 0/false/empty; no new table and no row created by a clear or a read. `STORAGE` maps the new ids to `TERMS`. Wire order, provenance and revisions as before; dates serialise as `"2027-03-01"`; money with two decimals as strings.
- **Validation (registry-driven, edit time)**: money as `asking_price` rules (`invalid_price`); integers as 2.4 (`invalid`, `invalid_range`); booleans as 2.4; enums `invalid_choice`; dates as above (add `invalid_date` to the messages catalogue); `not_applicable` when the Draft's purpose does not match the field (judged from the Draft's identity; a null on a not-applicable field is a harmless no-op as before); cross-field: `advance_rent` and `security_deposit` have no relation to the rent amount (no cross-check). Changing nothing about the purpose of a Draft (identity is immutable) means a Draft can never hold terms of the other purpose.
- **Unsupported (deferred) terms**: `payment_type`, `down_payment`, `total_installment_price`, `monthly_installment`, `remaining_installments`, `ownership_document_type`, `title_document_type`, `approval_information`, `installment_available` and any id outside the registry are `unknown_field` (arrive in Epic 11); add an explicit test with these ids.
- **Rent Draft without rental period**: saving is allowed; the registry marks `rental_period` `required_for_publication` and applicable to rent (the publish-time message is Story 2.15's); add a test that `registry` and the Draft view expose `rental_period` null for a rent Draft with `price_basis rental_period` and nothing else, and that nothing assumes `month`.
- Docs (`docs/listings.md`, `docs/registry.md`), README, regenerated `openapi.yaml`; `RESPONSE_SHAPE_VERSION` bumped only if the registry payload gains keys (a date field serves `min`/`max` strings in the existing keys: no bump); every earlier test keeps passing; import-linter unchanged. Apply the lessons of Stories 2.4 and 2.5 from the start (strict shapes, whole-body echo checks minus the request id only, after-commit event tests with real on_commit capture, null-on-not-applicable no-op documented and tested, stored out-of-range value revalidation, column-fit guard test for every registry money/decimal/integer/date field vs its column, loader tests with message matches).

**Always (frontend, after the backend is merged and `npm run api:sync`):**
- The generic Details section gains: the `date` control (a native `<input type="date">` whose value is the ISO string; typed text from browsers without date support is sent as typed so the server judges it; no `Date` parsing or timezone conversion anywhere, comparison by string), labels for every new field ("Security deposit (PKR)", "Advance rent (PKR)", "Minimum lease (months)", "Notice period (days)", "Electricity included", ..., "Pets allowed", "Occupant restrictions", "Price negotiable", "Possession availability", "Available from") with readable option labels, a "Terms" presentation: fields whose registry applicability lists purposes only (the terms fields) are shown in a separate fieldset "Terms for sale" / "Terms for rent" grouped by requirement inside it, before the property details; seller-declared labelling for `possession_availability` as in 2.5; the `invalid_date` message; conflict rows with formatted latest values (dates as ISO text, money "PKR 25,000.00", booleans Yes/No); the Draft summary lists answered terms. A sale Draft never shows rent fields and vice versa; deferred terms never appear. Stub: registry v6, strict backend-mirroring rules (date window and shape, money, integer ranges, purposes applicability, null no-op), presets for sale and rent drafts with terms. Tests per matrix row, axe at both widths, README. All earlier lessons apply (owning-element assertions, no serial mode, wait for counters before negatives, aria-disabled, low-entropy values, fail-closed registry handling, conflict banner clearing).

**Never:** assume a rental period (monthly or otherwise); store 0/false/empty for unanswered; accept installment, payment-type or title-document terms; accept timestamps for dates; parse dates with `Date` in the client; show a platform badge on a seller-declared term; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Sale terms | sale Draft: `asking_price`, `price_negotiable true`, `possession_availability within_one_month` | stored on `ListingTerms` | N/A |
| Rent terms | rent Draft: rental amount, `rental_period month`, `security_deposit`, `availability_date "2027-03-01"`, `advance_rent`, `minimum_lease_months 12`, `notice_period_days 30`, utilities, `pets_allowed false`, `occupant_restrictions families_only` | stored, `false` kept as false | N/A |
| Unanswered | nothing saved or cleared | all new fields `null`, never 0/false/empty | test |
| Purpose rules | sale + `security_deposit`; rent + `possession_availability`; either + `price_negotiable` | `not_applicable` for the first two; the third accepted | envelope |
| Dates | `"2027-02-30"`, `"2027-3-1"`, `"2027-03-01T00:00:00"`, `20270301`, `"٢٠٢٧-٠٣-٠١"`, a JSON number | `invalid` | envelope |
| Date window | `"1999-12-31"`, `"2101-01-01"` | `invalid_date` | envelope |
| Money | negative, 3 decimals, letters, over max | `invalid_price` / `invalid` | envelope |
| Integers | `minimum_lease_months 0 / 121`, `notice_period_days -1 / 366`, `"12"`, `12.0` | `invalid_range` / `invalid` | envelope |
| Enums and booleans | `occupant_restrictions "any"`, `pets_allowed "yes"` | `invalid_choice` / `invalid` | envelope |
| Deferred terms | `payment_type`, `down_payment`, installment ids, `title_document_type`, `approval_information` | `unknown_field`; nothing written | envelope |
| Rent without a period | rent Draft, rental period never set | saving fine; `rental_period` null; nothing assumes month | test |
| Clear | `security_deposit null` | column null, provenance removed, revision +1 | N/A |
| Seller-declared | `possession_availability` | registry flag true; not AI-extractable | test |
| Older registry | Draft under version 5 | revalidated under 6 | test |
| Stale / replay / concurrency | as 2.2 | 409 with `current` | test |
| Contract | `openapi.yaml`, registry payload | new fields and the date type served; fresh | test |
| Frontend: sale vs rent | open each | sale shows sale terms only, rent shows rent terms only, in a "Terms" fieldset before the details | N/A |
| Frontend: controls | money, integer, boolean, enum, date | right control per type; "Not answered" first; date as native date input | N/A |
| Frontend: errors and conflicts | codes incl. `invalid_date`, 409 | text under the control, rows with formatted latest values, input kept | N/A |
| Frontend: seller-declared | `possession_availability` | "Seller-declared" tag and helper; no EstateAI badge | N/A |
| Frontend: untouched / privacy / a11y | any | nothing sent unchanged; no storage/console leak; axe clean desktop and mobile | test |

</frozen-after-approval>

## Code Map

- Backend: `apps/catalog/registry.py`, `registry/fields.yaml`, `fields.lock`; `apps/listings/{models,fields,validation,services,selectors}.py`, `migrations/0006_terms.py`; `apps/presentation/api/{listings,registry}.py`; `apps/kernel/errors.py`; docs, `openapi.yaml`, tests
- Frontend: `src/lib/{use-registry,listing-details,listing-labels,listing-messages}`, `src/app/(app)/account/listings/[id]/{details-section,draft-editor}.tsx`, `tests/e2e/{stub-backend.mjs,listing-edit.spec.ts}`, README

## Tasks & Acceptance

**Execution:**
- [ ] Backend: registry v6 with the `date` type and terms fields, terms columns and constraints, validation, wire, docs, tests
- [ ] Frontend: date control, labels, Terms presentation, stub, tests, README

**Acceptance Criteria:**
- Given a sale Draft, when I edit terms then the total Asking price is required and price negotiable and possession availability are accepted, stored as typed nullable columns on `ListingTerms`.
- Given a rent Draft, when I edit terms then the rental amount and an explicit rental period are required (never assumed monthly), security deposit, availability date and furnishing are recommended, and advance rent, minimum lease, notice period, maintenance charges, included utilities, pets allowed and occupant restrictions are optional.
- Given deferred terms such as payment type or installments, when submitted they are rejected as unsupported.

## Implementation Notes

Decisions beyond the story text: utilities are four independent tri-state booleans (no list type needed); `possession_availability` is seller-declared; dates are plain calendar dates in a fixed window; maintenance charges and furnishing for rent reuse the existing fields with their existing Subtype applicability.

## Spec Change Log

## Review Triage Log

Backend half (three reviewers). Patched: an explicit registry marker `section` (`terms`/`details`) now groups the terms fields (the spec's "purposes without subtypes" rule excluded `price_negotiable`); registry shape version 5; the after-commit rollback test now really fails without a rollback; ETag/date-bound tests do what their names say; unquoted YAML dates get a clear loader error; docs corrected (no purpose CHECK, zero deposit allowed, neutral invalid_price text, date bounds are ISO text, past availability dates allowed). The frontend grouping rule in this spec is superseded by the `section` marker. Deviations accepted: no purpose CHECK (the column lives on another table), `invalid_price` text made neutral. Verification: lint-imports 13 kept, ruff, pytest 3746 passed, no migration drift, openapi regenerated.

## Design Notes

Terms stay on one row (`ListingTerms`) so later search and the valuation model read price and terms from one place. The frontend gets a "Terms" fieldset only by reading registry applicability (purposes without subtypes), keeping everything data-driven.

## Verification

**Backend:** `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check .; uv run ruff format --check .; env -u DJANGO_SETTINGS_MODULE uv run pytest -q; uv run python manage.py makemigrations --check --dry-run; uv run python manage.py spectacular --validate --file openapi.yaml` (openapi regenerated deliberately).
**Frontend:** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` (full run in the background; never two at once).
