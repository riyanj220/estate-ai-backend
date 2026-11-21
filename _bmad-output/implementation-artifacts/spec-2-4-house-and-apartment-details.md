---
title: 'Story 2.4: House and Apartment details (backend half, then frontend half)'
type: 'feature'
created: '2026-11-21'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-3-area-and-units.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-2-conflict-safe-editing.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-2-conflict-safe-editing-frontend.md'
  - '{project-root}/_bmad-output/planning-artifacts/prds/prd-estate-ai-backend-2026-11-18/addendum.md (section 8, the attribute catalogue)'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/registry.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A House or Apartment Draft cannot yet carry the details buyers expect (bedrooms, bathrooms, floors, condition, furnishing and so on), and the editor shows only the common fields.

**Approach:** Follow the Story 2.2 and 2.3 pattern again (registry entries + `STORAGE` entries + one new table + migration + validation + wire shape), adding the residential attribute catalogue as registry fields with applicability per Subtype, stored in a lazily created `ListingResidential` record, saved through the existing `apply_updates` and `PATCH`. The frontend half builds a GENERIC registry-driven "Details" section (integer, yes/no/unknown, choice, decimal controls chosen from the registry definitions and shown only when applicable to the Draft's Subtype), reused by Stories 2.5 and 2.6. Backend first and merged; then the frontend in `/home/riyan/Riyan/projects/estate-ai-frontend`.

## Boundaries & Constraints

**Always (backend):**
- **Registry version 4** (bump, `scripts/registry_lock.py`). New field types: `integer` (`min`, `max`) and `boolean` (tri-state: true, false, or unknown = `null`). The loader, serializer for `GET /api/v1/registry` (bump `RESPONSE_SHAPE_VERSION` if the payload shape changes) and the OpenAPI help text handle them. All new fields are class public, `ai_extractable`, not `valuation_input` except where noted, and carry applicability by Subtype (`house` or `apartment`); a field sent for a Draft whose Subtype it does not apply to is `not_applicable` (judged from the Draft's identity, which never changes).
  - Both house and apartment: `bedrooms` (integer 0 to 50, required_for_publication, `searchable`, `valuation_input`), `bathrooms` (integer 0 to 50, required_for_publication, `searchable`, `valuation_input`), `construction_year` (integer 1900 to 2100, recommended for house and optional for apartment: the registry has one requirement per field, so mark it `recommended`; `valuation_input`), `condition` (enum `new`, `excellent`, `good`, `needs_renovation`; recommended; `valuation_input`), `furnishing` (enum `unfurnished`, `semi_furnished`, `furnished`; recommended; `searchable`), `parking_spaces` (integer 0 to 50; recommended; `searchable`), `occupancy_status` (enum `owner_occupied`, `tenant_occupied`, `vacant`; recommended), `covered_area_sqft` (decimal, scale 4, min "0.0001", max "9999999999.9999"; recommended; `valuation_input`; the label says square feet and it never depends on `area_unit`).
  - House only: `total_floors` (integer 1 to 200, recommended), `kitchens` (integer 0 to 50, recommended), `construction_status` (enum `completed`, `under_construction`, `grey_structure`; recommended), and optional integers `drawing_rooms`, `dining_rooms`, `tv_lounges`, `study_rooms`, `prayer_rooms`, `store_rooms`, `laundry_rooms`, `powder_rooms`, `servant_quarters` (each 0 to 50) and optional boolean `basement`.
  - Apartment only: `floor_number` (integer 0 to 200, 0 = ground; recommended), `building_floors` (integer 1 to 200, recommended), `lift`, `backup_electricity`, `building_security` (booleans, recommended), optional booleans `balcony_or_terrace`, `building_lobby`, `intercom`, and optional money `maintenance_charges` (PKR, same rules as `asking_price` but class public, min "0", max "999999999999.99", per month stated in the label).
  - Cross-field rule: for an apartment, `floor_number` must not exceed `building_floors` when both are set in the resulting state (field code `invalid_floor` on `floor_number`); no other cross-field rules.
- **Storage**: a new one-to-one `ListingResidential` model inside `listings` (typed nullable columns, one per field above, `updated_at`), created lazily ONLY when the first residential attribute is saved with a non-null value (a Draft never gets a row from clears or from create; clearing a field in an existing row sets the column null), registry-generated CHECK constraints (integer bounds, enum sets, decimal bounds, money bounds) frozen in migration `0004_residential` with drift tests like 2.1 to 2.3; booleans are nullable boolean columns, never defaulted; unanswered is ALWAYS `null`, never `0` or `false`. `STORAGE` gains a `RESIDENTIAL` table kind; `EDITABLE_FIELDS`/`WIRE_FIELDS` extended in registry order; provenance and revisions work as in 2.2. Money serialises as a string with two decimals; decimals as strings with the stored scale; integers as JSON integers; booleans as JSON booleans; all `null` when unknown.
- **Validation (registry-driven, edit time)**: integers only as JSON integers (no strings, no floats, no booleans: `invalid`; out of range `invalid_range`); booleans only JSON true/false (`invalid` otherwise); enums `invalid_choice`; decimal and money as in 2.2/2.3 (`invalid`, `invalid_area` for the covered area, `invalid_price` for maintenance charges); `not_applicable` for the wrong Subtype; `null` clears. Add the new field codes (`invalid_range`, `invalid_floor`) to the kernel messages catalogue. `covered_area_sqft` greater than the listing's own derived `area_sqft` is NOT rejected (units may be unknown or loose).
- **Events**: changes to a `valuation_input` field emit `listing.model_inputs_changed` as before (one event per save).
- **Wire (AD-24)**: all new fields appear flat in `fields` (null when unknown or not applicable to the Subtype: applicable fields are described by the registry); provenance for each editable field written; the listing resource and OpenAPI document the new fields; existing contract tests updated for additions only.
- Tests (at least: every field's accept and reject, bounds, the not_applicable matrix for both Subtypes and the Subtype-specific sets, lazy row creation, null never becomes 0/false, clear semantics, the floor rule, 409/concurrency unchanged, older-registry revalidation, drift/constraints, OpenAPI and registry contract), docs (`docs/listings.md`, `docs/registry.md`), README and `openapi.yaml` regenerated; import-linter and every earlier test keep passing.

**Always (frontend, after the backend is merged and `npm run api:sync` run):**
- A GENERIC registry-driven "Details" section in the Draft editor, built from the registry field definitions applicable to the Draft's Subtype (and purpose), grouped as "Required before you publish", "Recommended" and "More details" by the registry requirement, in registry order; control per type: `integer` text input with `inputmode="numeric"` (digits only; text matching `^(0|[1-9]\d{0,3})$` is sent as a JSON integer; any other text is sent as the raw string so the server refuses it as `invalid`, consistent with earlier stories; never `Number()` on arbitrary text), `boolean` a three-option select "Not answered / Yes / No" (null / true / false), `enum` a select with "Not answered" first (null), `decimal` (covered area) and `money` (maintenance charges) text inputs with the 2.2/2.3 string rules. Labels come from a label map keyed by registry id (fallback: the id humanised), choices from the registry; a field the registry does not know gets no control; a stub registry with other bounds or options changes the form and the hints. Same single Save, same changed-fields-only PATCH, 400 mapping (`invalid`, `invalid_range`, `invalid_floor`, `invalid_area`, `invalid_price`, `invalid_choice`, `not_applicable` and generic), conflict flow with `Keep mine` / `Use latest` rows and formatted latest values for the new fields, typed-input preservation, bfcache, no storage, accessible grouping (fieldset/legend), axe at both widths. A Draft whose Subtype shows different applicable fields simply shows its own set; "Not answered" is the only representation of unknown and an untouched field is never sent. The Draft summary lists answered details only.
- Playwright: stub gains the new fields with strict rules mirroring the real backend (integers as JSON integers, bounds, enums, floor rule, not_applicable by Subtype), tests per matrix row, README.

**Never:** store zero or false for an unanswered attribute; create the residential row from a clear or a read; accept strings for integers or booleans; accept attributes outside the registry; send untouched fields; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| House details | `bedrooms 4, bathrooms 3, total_floors 2, kitchens 1` | stored, row created lazily, wire shows ints | N/A |
| Apartment details | `bedrooms 2, bathrooms 2, floor_number 5, building_floors 12, lift true` | stored | N/A |
| Unanswered stays null | a Draft never saved with details; or field cleared | every residential field `null`, no row until first non-null save | test |
| Not zero/false | `bedrooms` omitted; `lift` omitted | null in storage and wire, never 0/false | test |
| Zero is valid when given | `bedrooms 0` (studio) | stored 0 | N/A |
| Clear | `kitchens null` | column null, provenance removed, revision +1 | N/A |
| Wrong Subtype | apartment + `kitchens`; house + `lift` | `not_applicable` on that field | envelope |
| Other category | plot or commercial Draft + `bedrooms` | `not_applicable` (these arrive with 2.5) | envelope |
| Types | `"3"`, `3.0`, `true`, `[]` for an integer; `"yes"`, `1` for a boolean | `invalid` | envelope |
| Range | `bedrooms 51`, `-1`, `construction_year 1899` | `invalid_range` | envelope |
| Enum | `condition "mint"` | `invalid_choice` | envelope |
| Covered area / charges | bad text, zero, over max | `invalid` / `invalid_area` / `invalid_price` | envelope |
| Floor rule | apartment `floor_number 10`, `building_floors 5` | `invalid_floor` on `floor_number`; rule uses the resulting state | envelope |
| Required for publication | bedrooms and bathrooms missing | saving allowed; registry marks them `required_for_publication` (enforced in 2.15) | N/A |
| Valuation event | bedrooms or covered area changes | one `listing.model_inputs_changed` after commit | test |
| Stale / replay / concurrency | as 2.2 | 409 with `current` | test |
| Older registry | Draft under version 3 | revalidated under 4 | test |
| Contract | `openapi.yaml`, `GET /registry` | new fields, types, applicability; fresh | test |
| Frontend: House vs Apartment | open each | only its applicable fields, grouped by requirement | N/A |
| Frontend: controls | integer, boolean, enum, decimal, money | right control per registry type; "Not answered" first | N/A |
| Frontend: untouched | no edits | nothing sent; "No changes to save." | N/A |
| Frontend: errors and conflicts | codes and 409 | text under the control, summary focus, rows with formatted latest values, typed input kept | N/A |
| Frontend: registry-driven | stub registry with other bounds or options | form follows it | test |
| Frontend: privacy and a11y | any state | no storage/console leak; axe clean desktop and mobile | test |

</frozen-after-approval>

## Code Map

- Backend: `apps/catalog/registry.py`, `registry/fields.yaml`, `fields.lock`; `apps/listings/{models,fields,validation,services,selectors}.py`, `migrations/0004_residential.py`; `apps/presentation/api/{listings,registry}.py`; `apps/kernel/errors.py`; docs, `openapi.yaml`, tests
- Frontend: `src/lib/{use-registry,listing-labels,listing-messages}`, a new generic details renderer under `src/app/(app)/account/listings/[id]/`, `draft-editor.tsx`, `tests/e2e/{stub-backend.mjs,listing-edit.spec.ts}`, README

## Tasks & Acceptance

**Execution:**
- [ ] Backend: registry v4 (`integer`, `boolean`), `ListingResidential`, validation, wire, docs, tests
- [ ] Frontend: generic Details section, stub, tests, README

**Acceptance Criteria:**
- Given a House or Apartment Draft, when I edit its details then bedrooms and bathrooms are required for Publication and the recommended and optional attributes are accepted and never block Publication.
- Given an attribute I leave unanswered, when it is saved or read it is null, never zero or false.
- Given the form, when I choose House versus Apartment only the fields relevant to that Subtype are shown, driven by the registry, and the residential record is created only when I first save a residential attribute.

## Implementation Notes

Decisions beyond the story text: `covered_area_sqft` is always square feet (no unit pairing); `construction_year` has one requirement level (`recommended`) in the registry for both Subtypes; plot dimensions for houses are left to a later registry addition; the generic Details renderer is designed to be reused unchanged by Stories 2.5 and 2.6.

## Spec Change Log

## Review Triage Log

Backend half (three reviewers). Patched: `invalid_floor` is reported on `floor_number` and also on `building_floors` when that was sent; echo-leak assertions restored to whole-body checks (only the request id removed, which had made a test flaky in CI); a floor-rule test that really reaches a violating stored pair; null on a not-applicable field pinned as a no-op; stored out-of-range value revalidated; docs and annotation fixes. Deferred: dead floor guard, floor equal to building floors accepted, RESPONSE_SHAPE_VERSION stays 3 (no new keys). Verification: lint-imports 13 kept, ruff, pytest 3208 passed, no migration drift, openapi regenerated.

Frontend half (three reviewers). Patched: integers up to 9 digits are sent as JSON integers so the server judges range (leading zeros and other text stay raw strings); linked floor errors clear only when a floor field changes, late 400s drop errors for fields edited since; unrepresentable stored values stay visible (stringified numbers, "(stored value)", "(no longer offered)"); money compared by registry scale with leading-zero text dirty; conflict handling when another session makes a detail inapplicable; the label-only claim is now proven for errors and conflict rows; hand-built id list shared and checked. Deferred: non-editable registry flag (not in the payload), summary heading association. Verification: lint, typecheck, build, api:check, full Playwright 922 passed.

## Design Notes

One table per concern (terms, area, residential; plots/commercial get theirs in 2.5). Tri-state booleans and nullable integers keep "unknown" honest for search and for the valuation model later.

## Verification

**Backend:** `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check .; uv run ruff format --check .; env -u DJANGO_SETTINGS_MODULE uv run pytest -q; uv run python manage.py makemigrations --check --dry-run; uv run python manage.py spectacular --validate --file openapi.yaml` (openapi regenerated deliberately).
**Frontend:** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` (full run in the background; never two at once).
