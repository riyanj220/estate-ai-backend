---
title: 'Story 2.5: Plot and Commercial details (backend half, then frontend half)'
type: 'feature'
created: '2026-11-21'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-4-house-and-apartment-details.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-3-area-and-units.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-2-conflict-safe-editing.md'
  - '{project-root}/_bmad-output/planning-artifacts/prds/prd-estate-ai-backend-2026-11-18/addendum.md (section 8, the attribute catalogue: Plot and Commercial rows)'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/registry.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Plot and Commercial Drafts cannot yet carry the details specific to their type, and buyers must never mistake a Plot File for a possession-ready plot or read an owner's legal claims as verified by EstateAI.

**Approach:** Follow the Story 2.4 pattern: registry fields with applicability by Subtype, new storage tables, validation, wire. Fields the catalogue shares between Residential and Commercial reuse the SAME registry ids and the existing columns. Introduce a registry flag `seller_declared` (legal, approval, possession and similar claims) that the frontend shows as a "Seller-declared" label and never as a platform badge, and an `allowed_values_by_subtype` map so a Plot File has its own possession vocabulary. Backend first and merged; the frontend half then needs only labels in the generic Details renderer plus the two registry features.

## Boundaries & Constraints

**Always (backend):**
- **Registry version 5** (bump, lock). New registry features: `seller_declared: true|false` (default false, served in the registry payload; loader validates it is a boolean) and `allowed_values_by_subtype` (a map from Subtype id to a subset of `allowed_values`, same loader rules as `allowed_values_by_purpose`; validation enforces it from the Draft's identity; served in the payload; bump `RESPONSE_SHAPE_VERSION` because the payload gains keys). New field types are not needed; reuse `integer`, `boolean`, `enum`, `decimal`, `money`.
- **Extended applicability of existing fields (same ids and columns, `applicability.subtypes` widened; the existing house/apartment rules unchanged)**: `condition`, `furnishing` (label for commercial: fit-out), `parking_spaces`, `occupancy_status`, `floor_number`, `building_floors` (optional for commercial) now also apply to `shop` and `office`; `lift` and `building_security` apply to `office` (and apartment as before) and `building_security` also to `shop`; `building_lobby`, `maintenance_charges` also to `shop` and `office`. The floor rule (floor not above building floors) applies to every Subtype where both fields apply.
- **New shared utility booleans** (tri-state, public, `ai_extractable`): `electricity` (recommended; plot, commercial), `water` (recommended for plot; optional for commercial), `gas` (optional; plot and commercial). They live in the existing unit-details table (document that it now holds the attributes shared by residential, plot and commercial units; the model name stays).
- **Plot-only fields** (new table `ListingPlot`, lazily created on the first non-null plot attribute, applicability `subtypes: [residential_plot, commercial_plot, plot_file]` unless stated): `plot_width` and `plot_depth` (decimal feet, scale 2, min "0.01", max "99999.99", recommended), `possession_status` (enum `possession_ready`, `possession_pending`, `file_only`; `seller_declared`; recommended; `allowed_values_by_subtype`: `plot_file` allows only `file_only` and the other two Subtypes allow `possession_ready`, `possession_pending`), `corner_plot` (boolean, recommended), `road_width_ft` (decimal feet, scale 2, min "1.00", max "999.99", recommended), `balloting_status` (enum `not_applicable`, `balloting_pending`, `balloted`; `seller_declared`; recommended), `sewerage` (boolean, recommended), `development_status` (enum `developed`, `partially_developed`, `undeveloped`; `seller_declared`; recommended), `approval_authority` (enum `lda`, `cda`, `kda`, `rda`, `fda`, `mda`, `gda`, `bda`, `peshawar_development_authority`, `private_society`, `none`, `other`; `seller_declared`; recommended); optional booleans `park_facing`, `main_boulevard`, `boundary_wall`, optional enum `orientation` (`north`, `south`, `east`, `west`, `north_east`, `north_west`, `south_east`, `south_west`), and conditional `plot_file_status` (enum `open_file`, `allocation_letter`, `possession_letter`; `seller_declared`; applies ONLY to `plot_file`; optional). The Plot File Subtype is never presented as possession-ready: the backend refuses `possession_ready` and `possession_pending` for a `plot_file` (`invalid_choice`) and refuses `file_only` for other Subtypes.
- **Commercial-only fields** (new table `ListingCommercial`, lazily created, applicability `subtypes: [shop, office]` unless stated): `business_use` (enum `retail`, `restaurant`, `office_work`, `clinic`, `warehouse`, `showroom`, `mixed`, `other`; `seller_declared` false; recommended), `frontage_ft` (decimal feet, scale 2, min "0.01", max "9999.99"; recommended; SHOP only), `road_access` (enum `main_road`, `secondary_road`, `inner_street`; recommended), optional: `ceiling_height_ft` (decimal scale 2, min "1.00", max "99.99"), `power_load_kw` (decimal scale 2, min "0.01", max "99999.99"), `three_phase_electricity` (boolean), `conference_rooms` (integer 0 to 50; office only), `business_centre_facilities` (boolean; office only).
- **Unsupported attributes**: ids outside the active registry (for example `installment_available`, `down_payment`, `title_document_type`, `mortgage_declaration`, `approval_reference_number`) are rejected `unknown_field` exactly as any unknown id; add an explicit test with those ids (they arrive in Epic 11).
- **Storage and constraints**: migration `0005_plot_commercial` (two new tables with typed nullable columns, registry-generated CHECKs frozen in the migration with drift tests, the floor CHECK kept); `STORAGE` gains `PLOT` and `COMMERCIAL` kinds; unanswered is `null` always (never 0/false); no row is created by a clear, a read or create_draft. Wire order and provenance as before; decimals serialise with their stored scale as strings.
- **Validation**: as 2.4 (integers JSON ints, booleans JSON booleans, decimals strict text with the area-style shape per scale `^\d{1,10}(\.\d{1,scale})?$` generalised from the registry scale, enums `invalid_choice`, ranges `invalid_range` for integers and `invalid_measure` for the new decimal-feet fields, `not_applicable` by Subtype, null a no-op when nothing is stored); `allowed_values_by_subtype` violation is `invalid_choice`. Add the `invalid_measure` code to the messages catalogue. `valuation_input` flags: `plot_width`, `plot_depth`, `road_width_ft`, `corner_plot`, `development_status`, `frontage_ft`, `condition` (already) are valuation inputs; the rest are not.
- **Docs and tests**: docs/listings.md, docs/registry.md (new flags/maps, version 5, the seller-declared rule), README, openapi.yaml regenerated; tests per matrix row; earlier tests keep passing; import-linter contracts unchanged.

**Always (frontend, after the backend is merged and `npm run api:sync` run):**
- The generic Details section (Story 2.4, `details-section.tsx`) must need only labels for the new fields. Add the label entries (humanised fallback otherwise), the Subtype-aware enum options (use `allowed_values_by_subtype[subtype]` when present, else `allowed_values_by_purpose`, else `allowed_values`), and a visible "Seller-declared" label next to every field whose registry `seller_declared` is true (text plus a short helper line: "You are stating this. EstateAI has not checked it."), never an EstateAI badge or check icon. A Plot File Draft shows only the `file_only` possession choice (pre-selecting nothing). The Draft summary marks seller-declared details as "Seller-declared". Money/decimal controls handle the feet fields with the 2.2/2.3/2.4 rules and the registry scale; the new error code `invalid_measure` gets text; the conflict flow, banner clearing, privacy and a11y rules stay as in 2.4. Commercial Drafts show the shared fields (condition, furnishing "Furnishing or fit-out", parking, occupancy, floor, building floors, lift, security, lobby, maintenance charges) per applicability. Stub: registry v5 with all fields, applicability, the by-subtype map and the flag, strict rules mirroring the backend, presets for plot, plot file, shop and office drafts. Tests per matrix row, axe at both widths, README.

**Never:** show a platform badge on any seller-declared attribute; let a Plot File carry `possession_ready`/`possession_pending`; accept unsupported legal/installment/mortgage attributes; store 0/false for unanswered; create rows from clears; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Plot details | residential plot: width, depth, `possession_ready`, `corner_plot true`, road width, `development_status developed`, `approval_authority lda` | stored in `ListingPlot` (row created lazily) | N/A |
| Plot File | `plot_file` + `possession_status file_only`, `plot_file_status allocation_letter` | stored | N/A |
| Plot File vs ready | `plot_file` + `possession_ready` / non-file + `file_only` | `invalid_choice` on `possession_status` | envelope |
| Plot file status elsewhere | residential plot + `plot_file_status` | `not_applicable` | envelope |
| Commercial details | shop: `business_use`, `frontage_ft`, `road_access`, condition, parking; office: `lift true`, `conference_rooms 3` | stored in `ListingCommercial` / shared columns | N/A |
| Subtype limits | office + `frontage_ft`; shop + `conference_rooms`; house + `business_use`; plot + `bedrooms`; commercial + `kitchens` | `not_applicable` | envelope |
| Shared fields | shop `floor_number 2`, `building_floors 3` | stored in the shared columns; floor rule applies | N/A |
| Floor rule | shop floor 5, building 3 | `invalid_floor` on the sent fields | envelope |
| Measures | `plot_width "0"`, `"12,5"`, 3 decimals, over max, JSON number | `invalid_measure` / `invalid` per shape rule; never echoed | envelope |
| Types and ranges | string for integer/boolean, `conference_rooms 51` | `invalid` / `invalid_range` | envelope |
| Enum | `approval_authority "xyz"` | `invalid_choice` | envelope |
| Unsupported attributes | `installment_available`, `down_payment`, `title_document_type`, `mortgage_declaration`, `approval_reference_number` | `unknown_field`; nothing written | envelope |
| Unanswered | nothing saved / cleared | all `null`, never 0/false; no plot/commercial row from a clear | test |
| Lazy rows | first non-null plot or commercial value | row created; unrelated edits and clears never create | test |
| Valuation event | `plot_width` / `frontage_ft` / `corner_plot` change | one `listing.model_inputs_changed` after commit; none for non-input fields | test |
| Stale / replay / concurrency / older registry | as 2.2 to 2.4 | 409 with `current`; revalidated under version 5 | test |
| Registry payload | `GET /api/v1/registry` | `seller_declared` and `allowed_values_by_subtype` served; shape version bumped; ETag changed; OpenAPI fresh | test |
| Frontend: Plot vs Plot File vs Shop vs Office | open each | only the applicable fields, grouped by requirement | N/A |
| Frontend: seller-declared | possession, approval, development, balloting, plot file status | "Seller-declared" label and helper line; no EstateAI badge | N/A |
| Frontend: Plot File possession | plot_file Draft | only `file_only` offered | N/A |
| Frontend: errors, conflicts, untouched | as 2.4 | same behaviour for the new fields incl. `invalid_measure` | N/A |
| Frontend: registry-driven | stub registry with other options / by-subtype map | form follows it | test |
| Frontend: privacy and a11y | any state | no storage/console leak; axe clean desktop and mobile | test |

</frozen-after-approval>

## Code Map

- Backend: `apps/catalog/registry.py`, `registry/fields.yaml`, `fields.lock`; `apps/listings/{models,fields,validation,services,selectors}.py`, `migrations/0005_plot_commercial.py`; `apps/presentation/api/{listings,registry}.py`; `apps/kernel/errors.py`; docs, `openapi.yaml`, tests
- Frontend: `src/lib/{use-registry,listing-details,listing-labels,listing-messages}`, `src/app/(app)/account/listings/[id]/{details-section,draft-editor}.tsx`, `tests/e2e/{stub-backend.mjs,listing-edit.spec.ts}`, README

## Tasks & Acceptance

**Execution:**
- [ ] Backend: registry v5 (`seller_declared`, `allowed_values_by_subtype`, applicability widening, plot and commercial fields), tables, validation, wire, docs, tests
- [ ] Frontend: labels, seller-declared labelling, by-subtype options, stub, tests, README

**Acceptance Criteria:**
- Given a Plot or Commercial Draft, when I edit its details the type-specific required, recommended and optional attributes are accepted and a Plot File is never presented as an ordinary possession-ready plot.
- Given legal, approval or possession attributes, when displayed they carry a "seller-declared" label and never an EstateAI badge.
- Given attributes outside the active registry, when a client submits them they are rejected as unsupported.

## Implementation Notes

Decisions beyond the story text: the enum value lists for possession, balloting, development, approval authority, business use and road access are the product vocabulary chosen here (extendable by registry edits); shared ids keep one meaning across Subtypes; the unit-details table also holds the attributes shared by residential, plot and commercial units.

## Spec Change Log

## Review Triage Log

Backend half (three reviewers). Patched: unit-neutral `invalid_measure` text, and the error code now comes from the registry (`error_code`) instead of a hard-coded id list; `values_for` returns nothing when a by-subtype map exists and no Subtype is given; stricter loader rules for `allowed_values_by_subtype` (category-scoped fields, overlap with the purpose map, dead values) with message-checked loader tests; a test that every registry decimal fits its model column; the five seller-declared claim fields are not `ai_extractable`; a tautological test fixed. Known limitation recorded in the docs: one requirement level per field, so `water` and `building_floors` are `recommended` for commercial too (the spec's "optional for commercial" cannot be expressed yet). Deferred: re-saving an unchanged floor on an already-invalid stored pair. Verification: lint-imports 13 kept, ruff, pytest 3432 passed, no migration drift, openapi regenerated.

## Design Notes

Seller-declared is a registry property so a later search or display layer can never forget it. The by-Subtype allowed-values map keeps the Plot File rule in data rather than in code branches.

## Verification

**Backend:** `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check .; uv run ruff format --check .; env -u DJANGO_SETTINGS_MODULE uv run pytest -q; uv run python manage.py makemigrations --check --dry-run; uv run python manage.py spectacular --validate --file openapi.yaml` (openapi regenerated deliberately).
**Frontend:** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` (full run in the background; never two at once).
