---
title: 'Story 2.8: Amenities and seller-declared nearby facilities (backend half, then frontend half)'
type: 'feature'
created: '2026-11-25'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-7-location-and-private-address.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-6-sale-and-rent-terms.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-4, AD-5, AD-22)'
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Story 2.8, vocabulary is fixed there)'
  - '{project-root}/docs/registry.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/reference-data.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Buyers filter on amenities and nearby facilities (FR-14). A Draft has none, and "the seller did not say" must never be read as "there is none".

**Approach:** Backend: an amenity catalogue in `catalog` (stable codes, kind, group), one new registry field `amenities` whose value is a map from catalogue code to a state, stored in its own table through the single `apply_updates` write path. Frontend (`/home/riyan/Riyan/projects/estate-ai-frontend`): an Amenities section on the Draft page grouped by kind, nearby facilities labelled "seller-declared". Backend first and merged, then frontend. Owner decision (OQ-1): the PRD default, five nearby types: mosque, park, gym, school, hospital; unmatched mentions are flagged, not stored (that is a later AI story, nothing here).

## Boundaries & Constraints

**Always (backend):**
- **Catalogue (`catalog`)**: model `Amenity` (uuid id, `code` stable snake_case unique, `label`, `kind` in {`on_property`,`community`,`nearby`}, `group` text, `sort_order`, `is_active`). Data migration seeds EXACTLY this vocabulary idempotently (re-run never duplicates nor overwrites an edited row):
  - on_property / utilities: `electricity_backup` Electricity backup, `broadband_internet` Broadband internet, `waste_disposal` Waste disposal.
  - on_property / building: `double_glazed_windows` Double-glazed windows, `central_air_conditioning` Central air conditioning, `central_heating` Central heating, `elevator` Elevator, `lobby` Lobby, `intercom` Intercom.
  - community / residential_community: `community_lawn` Community lawn, `swimming_pool` Swimming pool, `community_gym` Gym, `daycare` Daycare, `childrens_play_area` Children's play area, `bbq_area` BBQ area.
  - community / community_services: `community_mosque` Community mosque, `medical_facility` Medical facility, `community_centre` Community centre.
  - on_property / business: `conference_room` Conference room, `business_centre` Business centre, `atm_in_building` ATM in building.
  - nearby / nearby: `nearby_mosque` Mosque, `nearby_park` Park, `nearby_gym` Gym, `nearby_school` School, `nearby_hospital` Hospital.
  Written only through `catalog.services` (+ the migration). Selector `list_amenities()` (active, ordered by kind order on_property, community, nearby then sort_order; plain values `{code,label,kind,group}`) and `amenity_codes()` for validation. Public endpoint `GET /api/v1/reference/amenities` (AllowPublic, explicit serializers, `{"items":[...]}`, plain strings never OpenAPI enums, small unpaginated). Update `docs/reference-data.md`.
- **Registry (version 8, bump `RESPONSE_SHAPE_VERSION` if the wire shape changes)**: new type `amenities` for the field `amenities` (class public, section `amenities`, optional, applicable to every purpose/category/subtype, `searchable` true, not sortable). The value is a JSON object `{code: state}` where state is `"present"` or `"none"`; a code that is absent from the object is UNANSWERED. A PATCH value replaces the whole map (so the revision stays conflict-safe); `null` or `{}` clears everything. Validation: unknown/inactive code => `invalid` on `amenities`; state other than the two strings => `invalid`; non-object => `invalid`; at most one entry per code, at most 40 entries; a kind `nearby` entry with state `none` is allowed ("explicitly none" is meaningful for nearby too). Unanswered is never converted to `none` and never stored as a row. Update `fields.lock` through `scripts/registry_lock.py`; the loader must accept the new type.
- **Storage (listings migration `0011_amenities`)**: `ListingAmenity` (listing FK, `amenity_code` text, `state` CHECK in {present,none}, unique `(listing, amenity_code)`, index on `(amenity_code, state)` for later search). Written only inside `apply_updates` (replace the listing's rows with the new map in the same transaction; same `base_revision`, 409 `stale_revision`, provenance, revision rows record the changed field with the map of codes and states, which are not private). No row means unanswered.
- **Reading**: the owner resource carries `fields.amenities` as the map (only answered codes; absent codes are simply not in the map). `PublicListingView` carries the same public map (class public). Each nearby entry exposes ONLY code and state: no distance, no verification flag, no computed value anywhere (response, projection, openapi, events). The `seller-declared` fact is a property of the catalogue kind `nearby` (the catalogue entry has `kind: nearby`), documented in `docs/listings.md` and `docs/registry.md`; clients must label it.
- **Events**: `listing.model_inputs_changed` includes `amenities` in its changed field names when the map changes (no distance concept exists).
- **Docs/tests**: `docs/registry.md`, `docs/listings.md`, `docs/reference-data.md`, `docs/authorisation-matrix.md`; tests for every matrix row (exact seed vocabulary equality, seed idempotency via MigrationExecutor, endpoint, validation, replacement, clearing, unanswered never becomes none, no distance/verification key anywhere in any shape including openapi, projection carries the map, concurrency of two writers, stale 409, revision rows, `fields.lock` freshness, openapi freshness). Lessons from earlier stories apply (whole-body checks minus the request id, no vacuous assertions, rollback tests that can fail, static neutral messages, low-entropy fake words).

**Always (frontend, after the backend is merged and `npm run api:sync`):**
- An **Amenities** section on the Draft page, generic registry-driven (`amenities` type added to the shared renderer, not a one-off): the catalogue is loaded from `/api/v1/reference/amenities` (loading/empty/failed states with Retry, fail-closed parsing), items grouped by kind ("On the property", "Community", "Nearby") with their group sub-headings. Each item is a three-state control (Present, None, Not answered), a real fieldset of radio buttons per item (keyboard operable, labelled), default Not answered, Not answered removes the code from the map. The Nearby group shows a visible "Seller-declared" label and helper text ("Declared by you, not verified. No distance is shown."); no distance or verification UI anywhere. Saves use the same `base_revision` flow, conflict handling and per-field error mapping (codes `invalid` only, never server text), unknown codes in a saved map (from a later catalogue) are kept untouched and shown as read-only "Other" entries rather than dropped. Stub backend updated (catalogue route, validation mirroring, exact vocabulary), privacy/regression tests, axe clean at desktop and mobile in empty, filled, error and conflict states, aria-describedby where needed, README, production-build check extended only if a proxy or caching behaviour is relied on.

**Never:** treat unanswered as absent or as none; store a distance, verification or score for a nearby facility; let a client invent catalogue codes; add external calls; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Select amenities | map of known codes with present/none | saved, revision +1, replaces the previous map | N/A |
| Unknown or inactive code | any | `invalid` on `amenities`, nothing written | envelope |
| Bad state / non-object / too many entries | any | `invalid`, nothing written | envelope |
| Clear | `null` or `{}` | all rows removed, unanswered | N/A |
| Code left out of the map | absent | unanswered, never `none`, never a row | N/A |
| Nearby entry | `nearby_school: present` | stored and shown with code/state only; no distance/verification | N/A |
| Public projection | published listing | map carried, nothing else per entry | test |
| Stale / concurrent | old `base_revision` | 409 `stale_revision` with `current` (owner only) | envelope |
| Catalogue list | GET | exact vocabulary, ordered, with kind and group | N/A |
| Seed | migrate twice | no duplicates, edited rows untouched | test |

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

## Verification

**Backend:** `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check .; uv run ruff format --check .; env -u DJANGO_SETTINGS_MODULE uv run pytest -q; uv run python manage.py makemigrations --check --dry-run; uv run python manage.py spectacular --validate --file openapi.yaml` (openapi regenerated deliberately).
**Frontend:** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && npm run check:prod-admin && npm run check:prod-listings && npm run check:prod-profile && PW_CHANNEL=chrome npx playwright test` (full run in the background, once; never two at once).
