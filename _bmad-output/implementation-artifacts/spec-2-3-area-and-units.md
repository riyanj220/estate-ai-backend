---
title: 'Story 2.3: Area and measurement units (backend half, then frontend half)'
type: 'feature'
created: '2026-11-21'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-2-conflict-safe-editing.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-2-conflict-safe-editing-frontend.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/registry.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Draft can carry title, description and price but not how big the property is. Buyers and search need an area in the unit sellers actually use (square feet, square yards, marla, kanal, acre) and a comparable normalised figure.

**Approach:** Follow the Story 2.2 pattern exactly (registry entry + one `STORAGE` entry + migration + service validation + wire shape, then the editor). Add area as registry fields saved through the existing `apply_updates` and `PATCH` path, with the unit stored explicitly and a derived square-feet figure computed by the service where the conversion is certain. Backend first and merged; then the frontend half in `/home/riyan/Riyan/projects/estate-ai-frontend`.

## Boundaries & Constraints

**Always (backend):**
- **Registry version 3** (bump, run `scripts/registry_lock.py`). New type `decimal` (with `min`, `max`, `scale`; money stays its own type). New fields, all public, applicable to every Category and Subtype, requirement `required_for_publication` for `area_value` and `area_unit` and `optional` for the rest:
  - `area_value` (decimal, `min "0.0001"`, `max "9999999999.9999"`, scale 4; `searchable`, `sortable`, `ai_extractable`, `valuation_input`, so changes emit `listing.model_inputs_changed`).
  - `area_unit` (enum `sqft`, `sqyd`, `marla`, `kanal`, `acre`; `searchable`, `ai_extractable`; `valuation_input`).
  - `marla_basis` (enum with the string values `"225"`, `"250"`, `"272.25"` meaning square feet per marla; applies only while the resulting `area_unit` is `marla` or `kanal` because a kanal is 20 marla; `ai_extractable`; `valuation_input`). It is described in the registry as "square feet in one marla".
  - `area_sqft` (decimal, scale 4, DERIVED and read-only: new registry flag `derived: true`, rejected on input as `not_editable`; `searchable`, `sortable`; `valuation_input` false because it is computed from the inputs). The loader rejects a `derived` field that is editable-mapped, and the lock/drift tests cover it.
- **Storage**: a new one-to-one `ListingArea` model inside `listings` (`area_value` NUMERIC(14,4), `area_unit`, `marla_basis` NUMERIC(6,2) nullable, `area_sqft` NUMERIC(20,4) nullable, `updated_at`) created lazily like terms, with CHECK constraints generated from the registry (value within bounds, unit and basis in the allowed sets, `marla_basis` only when unit is marla or kanal, `area_sqft` null when not computable) frozen in migration `0003_area` with drift tests as in 2.1/2.2. `STORAGE` gains an `AREA` table kind; `EDITABLE_FIELDS` gains `area_value`, `area_unit`, `marla_basis`; `area_sqft` is a derived wire field only (never in `EDITABLE_FIELDS`). `WIRE_FIELDS` order puts the four area fields after the price fields.
- **Normalised area (service, exact decimal arithmetic, no float anywhere)**: `sqft`: value; `sqyd`: value x 9; `acre`: value x 43560; `marla`: value x basis; `kanal`: value x 20 x basis. Marla/kanal with no basis, a null value or a null unit give `area_sqft = null` (publication is NOT blocked by an unknown basis; the Listing is simply excluded from area-range filtering and has no estimate; document that this exclusion and the missing estimate are implemented by later stories reading `area_sqft is null`). Round half up to 4 decimals. `area_sqft` is recomputed by the service on every change to value, unit or basis, inside the same transaction, and is never accepted from a client.
- **Validation (registry-driven, at edit time as well as for later publication)**: `area_value` must match `^\d{1,10}(\.\d{1,4})?$` (text or JSON string; JSON numbers/floats refused like money; commas refused), greater than 0 (zero and negative: field code `invalid_area`), within the registry bounds; `area_unit` from the enum (`invalid_choice`); `marla_basis` from its enum, only the exact strings are accepted (`invalid_choice` otherwise, including JSON numbers). Cross-field, evaluated against the RESULTING state: a `marla_basis` while the resulting unit is not marla or kanal is `not_applicable` on `marla_basis`; when an edit changes the unit away from marla/kanal while a basis is stored, the service clears the basis (stored data always stays consistent) and the response shows it as `null`. `null` clears any area field. A value without a unit, or a unit without a value, is allowed in a Draft (completeness is a Story 2.15 publication matter) and `area_sqft` is then null.
- **Wire (AD-24)**: `fields` gains `area_value` (string serialised exactly as stored, e.g. `"10.0000"`; the client formats), `area_unit`, `marla_basis` (the enum strings exactly `"225"`, `"250"`, `"272.25"`: stored as NUMERIC and mapped back to the enum string), `area_sqft` (string, `null` when not computable), all `null` when unknown; provenance covers the three editable fields (not `area_sqft`); revisions record the editable changes plus `area_sqft` as a normal change entry when it changed. Registry `GET /api/v1/registry` serves the new fields including `derived`, `scale`, units list; `RESPONSE_SHAPE_VERSION` bump only if the shape of the payload changed (it did: new `scale`/`derived` keys, so bump and update the ETag tests).
- Tests, docs (`docs/listings.md`, `docs/registry.md`), `openapi.yaml`, authorisation matrix, README regenerated; at least 20 conversion cases (each unit, each basis, kanal, acre, rounding, large values), a property test that `area_sqft` always equals the formula, concurrency/409 unchanged, the 2.2 tests updated for additions only, import-linter contracts unchanged and passing.

**Always (frontend, after the backend is merged and `npm run api:sync` run):**
- An "Area" block in the Draft editor (same form, same single Save, same revision/409/typed-input rules as Story 2.2; unchanged-field logic, error mapping, conflict rows with `Keep mine`/`Use latest` all apply to the new fields): Area value (text input, `inputmode="decimal"`, digits and an optional decimal point, no commas, string only, never a JS number), Unit (select from the registry: Square feet, Square yards, Marla, Kanal, Acre), and Marla basis (select, shown only when the unit is marla or kanal; options "225 sq ft", "250 sq ft", "272.25 sq ft" plus a first option "I don't know yet", meaning null). When marla or kanal is chosen show the text: "A marla is not the same size everywhere. Tell us how many square feet your marla is. If you leave it unknown you can still publish, but your listing will not appear when buyers filter by area and it will have no price estimate." The derived `area_sqft` is shown read-only as "About 2,722.50 sq ft" (grouping by string operations, no JS number) when not null and a short "Not available until the marla size is known" note for marla/kanal without basis. Field codes `invalid_area` and the existing ones map to human text; the area value hint states the bounds from the registry; limits and options come from the registry hook (a stub registry with other values changes the form). Changing the unit away from marla/kanal hides the basis control and the form drops its typed basis (sends only changed fields; the server clears the stored one).
- Playwright: stub gains area fields and rules (strict like the real backend), tests per matrix row, axe at both widths, README. No serial mode, no commas accepted, fake low-entropy values.

**Never:** floats for areas or conversions; accept `area_sqft` from clients; block saving or later publication on an unknown basis; show the conversion as exact when `area_sqft` is null; write area outside `apply_updates`; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Save sqft | `area_value "1200"`, `area_unit sqft` | stored, `area_sqft "1200.0000"` | N/A |
| Square yards | `10`, `sqyd` | `area_sqft "90.0000"` | N/A |
| Acre | `2`, `acre` | `area_sqft "87120.0000"` | N/A |
| Marla with basis | `5`, `marla`, `marla_basis "225"` | `area_sqft "1125.0000"` | N/A |
| Kanal with basis | `1`, `kanal`, `"272.25"` | `area_sqft "5445.0000"` | N/A |
| Marla, unknown basis | `5`, `marla`, no basis | saved; `area_sqft null`; revision +1 | N/A |
| Basis later added | basis set in a second edit | `area_sqft` recomputed in the same save | N/A |
| Zero or negative | `"0"`, `"-5"` | `invalid_area` on `area_value`; nothing written | envelope |
| Bad value text | `"12,5"`, `"1e3"`, `"abc"`, JSON number 5, 5 decimals, over max | `invalid` / `invalid_area` per rule; never echoed | envelope |
| Bad unit or basis | `"hectare"`, basis `"300"`, basis `225` as a number | `invalid_choice` | envelope |
| Basis on a non-marla unit | `sqft` + basis | `not_applicable` on `marla_basis` | envelope |
| Unit changed away | marla with basis, then `sqft` | basis cleared (null), `area_sqft` recomputed | N/A |
| Value without unit | only `area_value` | saved; `area_sqft null` | N/A |
| Clear | `area_value null` | cleared, `area_sqft null`, provenance entry removed | N/A |
| Derived field sent | `area_sqft` in the body | `not_editable`; nothing written | envelope |
| No change | same values | 200, revision unchanged | N/A |
| Model-input event | value, unit or basis changes | `listing.model_inputs_changed` after commit | test |
| Older registry | Draft under version 2 | revalidated under 3, `registry_version` advanced | test |
| Stale revision / replay / concurrency | as 2.2 | 409 with `current` | test |
| Wire/registry | `GET /registry`, listing resource | new fields, `derived`, `scale`; openapi fresh; ETag changed | test |
| Frontend: marla chosen | unit marla | basis control and the explanation text appear; unknown allowed | N/A |
| Frontend: not marla/kanal | unit sqft | basis control hidden, not sent | N/A |
| Frontend: derived shown | `area_sqft` set / null | "About ..." / "Not available until the marla size is known" | N/A |
| Frontend: validation | `invalid_area`, `invalid_choice`, `not_applicable` | text under the right control, summary focus, input kept | N/A |
| Frontend: conflict | another session changed area | same flow as 2.2 incl. formatted latest values | N/A |
| Frontend: privacy and a11y | any state | no storage/console leaks; axe clean desktop and mobile | test |

</frozen-after-approval>

## Code Map

- Backend: `apps/catalog/registry.py`, `registry/fields.yaml`, `fields.lock`, `scripts/registry_lock.py`; `apps/listings/{models,fields,validation,services,selectors}.py`, `migrations/0003_area.py`; `apps/presentation/api/{listings,registry}.py`; `docs/`, `openapi.yaml`, `tests/`
- Frontend: `src/lib/{use-registry,listing-labels,listing-messages}`, `src/app/(app)/account/listings/[id]/draft-editor.tsx`, `tests/e2e/{stub-backend.mjs,listing-edit.spec.ts}`, README

## Tasks & Acceptance

**Execution:**
- [ ] Backend: registry v3 (`decimal`, `derived`), `ListingArea`, conversions, validation, wire, docs, tests
- [ ] Frontend: Area block, marla basis explanation, derived display, error mapping, stub and tests

**Acceptance Criteria:**
- Given a Draft, when I set area value and a unit, then NUMERIC values and the explicit unit are saved with a derived normalised area where the conversion is certain.
- Given marla (or kanal), when I set a basis of 225, 250 or 272.25 the normalised area is computed; when the basis is unknown publication is not blocked and the normalised area is null.
- Given zero or negative area, it is rejected.
- Given the frontend, when I choose marla I am asked for the basis and told what happens if I leave it unknown.

## Implementation Notes

Decision beyond the story text: the basis also applies to kanal (a kanal is 20 marla), since a kanal is not certain without the marla size.

## Spec Change Log

## Review Triage Log

## Design Notes

Area is its own table (not more `ListingTerms` columns) so later detail tables (2.4, 2.5) follow one pattern: one table per concern, one `STORAGE` kind each. The derived square-feet value is stored (not computed on read) so later search can index and sort on it. Acre is exact (43,560 sq ft); square yards exact (9); marla and kanal depend on the owner's stated basis.

## Verification

**Backend:** `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check .; uv run ruff format --check .; env -u DJANGO_SETTINGS_MODULE uv run pytest -q; uv run python manage.py makemigrations --check --dry-run; uv run python manage.py spectacular --validate --file openapi.yaml` (openapi regenerated deliberately and committed).
**Frontend:** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` (full run in the background; never two at once).
