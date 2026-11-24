---
title: 'Story 2.7: Public location and Private address (backend half, then frontend half)'
type: 'feature'
created: '2026-11-24'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-11-city-reference-data.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-6-sale-and-rent-terms.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-12-visibility-policy-and-public-projections.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-4, AD-5, AD-22, AD-24, AD-26)'
  - '{project-root}/docs/registry.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/reference-data.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Draft has no place. Buyers need the area, but the owner's exact address must never become public.

**Approach:** Backend: seed Karachi localities (owner approved drafting the list himself, see below) in `catalog`, add reference-checked `city` and `locality` plus public location text (`society`, `phase`, `sector`, `block`) and PRIVATE address fields (`street`, `house_number`, `latitude`, `longitude`) to the Field registry, store the private ones in a separate table, and keep them out of every public shape. Frontend (`/home/riyan/Riyan/projects/estate-ai-frontend`): a Location section on the Draft page with visibly separated "Public location" and "Private address (only you can see this)" parts. Backend first and merged, then frontend.

## Boundaries & Constraints

**Always (backend):**
- **Localities (catalog)**: model `Locality` (uuid id, `city` as a plain uuid column or in-app FK to `City`, `name` 2..80 chars like cities, `kind` in {`area`,`society`}, `is_active`, created_at; case-insensitive unique `(city, lower(name))`; kind CHECK). Data migration `0003_seed_karachi_localities` seeds the list below idempotently (re-run never duplicates nor overwrites an edited row), written only through `catalog.services` (+ the migration), actor-first `create_locality(actor, city_id, name, kind)` for administrators with audit `locality.created` like `create_city`. Selectors: `list_localities(city_id)` (active, ordered by name case-insensitively, plain values `{id, name, kind}`), `get_locality(locality_id)` (active only; also returns its city id). Public endpoint `GET /api/v1/reference/cities/{city_id}/localities` (AllowPublic, explicit serializers, `{"items":[{"id","name","kind"}]}`, plain strings never OpenAPI enums for names, `404 not_found` for an unknown/inactive/malformed city, small bounded list, unpaginated). Update `docs/reference-data.md`.
- **Seed list (approved by the owner, drafted by Claude; shown to him in the PR/hand-off, editable later by migration or admin story)** — Karachi **areas**: Gulshan-e-Iqbal, Gulistan-e-Jauhar, North Nazimabad, Nazimabad, Federal B Area, Clifton, Saddar, Bahadurabad, PECHS, Tariq Road, Korangi, Landhi, Malir, Shah Faisal Colony, North Karachi, New Karachi, Orangi Town, Liaquatabad, Lyari, Gulberg, Gulshan-e-Maymar, Scheme 33, Surjani Town, Garden, Jamshed Town, Baldia Town, SITE, Manghopir, Kemari, Zamzama, Bath Island, Defence View, Block 13-D Gulshan, Safoora Goth, Gadap Town, Sharfabad, Soldier Bazaar, Mehmoodabad, Clifton Block 5. Karachi **societies**: DHA Phase 1, DHA Phase 2, DHA Phase 2 Extension, DHA Phase 4, DHA Phase 5, DHA Phase 6, DHA Phase 7, DHA Phase 8, DHA City Karachi, Bahria Town Karachi, Naya Nazimabad, Askari 4, Askari 5, Askari 11, Falcon Society, Saima Arabian Villas, KDA Scheme 1, KDA Scheme 33 Society, Karachi Cooperative Housing Society, KAECHS, Gulshan-e-Hadeed, Malir Cantonment, Cantt Karachi, Emaar Crescent Bay, Sunset Boulevard, Gulistan-e-Johar Society, Precinct 1 (Bahria), Pakistan Employees Cooperative Housing Society, Memon Society, Al-Hilal Society, Taj Complex, Federal B Area Society. (Remove any exact duplicate name within the city; names must satisfy the City name pattern, so use the plain-letter forms of anything with digits/parentheses that the pattern rejects, or relax the Locality name pattern to also allow digits, parentheses are NOT allowed.) Cities other than Karachi get no localities.
- **Registry (version 7, `RESPONSE_SHAPE_VERSION` bump if the wire shape changes)**: new type `reference` with `reference: city` | `locality` (value stored as the reference's uuid; validated through the `catalog` selectors; wire value is the uuid string; unknown/inactive/malformed => `invalid` code; a locality must belong to the listing's chosen city, else `invalid` on `locality` with a distinct message code `locality_not_in_city`; changing `city` clears `locality` in the same write (and reports it in the update's changed fields) so a stale pair can never be stored). New fields (all applicable to every purpose/category/subtype): `city` and `locality` (`required_for_publication`, class public, `searchable` true), `society`, `phase`, `sector`, `block` (text, optional, max 60, class public, trimmed, empty is null, `ai_extractable` true; they are part of the public location), and the PRIVATE address `street` (text max 120), `house_number` (text max 30), `latitude` (decimal -90..90, scale 6), `longitude` (decimal -180..180, scale 6), all class `private`, optional, NOT searchable/sortable/valuation_input/ai_extractable (the loader already forbids the first three on private; also forbid `ai_extractable` for private fields). Coordinates are both-or-neither (one without the other => `invalid` on the missing one). Text fields pass the existing contact-detail check (no phone/email in street or location text) and the existing text constraints. Update `fields.lock` through `scripts/registry_lock.py`.
- **Storage (listings migration `0010_location`)**: `ListingLocation` (one row per Listing, created lazily on the first non-null write: `city_id` uuid, `locality_id` uuid, `society`, `phase`, `sector`, `block`; indexes on `(city_id, locality_id)` for later search) and a SEPARATE `ListingPrivateAddress` table (listing 1:1, `street`, `house_number`, `latitude`, `longitude` with CHECK bounds, both-or-neither CHECK on coordinates). Written only through the single `apply_updates` write path (same `base_revision`, 409 `stale_revision`, provenance, revision rows that record field NAMES only never values, `null` on a not-applicable field is a harmless no-op, unanswered stays null). The private table is never joined into any public selector or projection.
- **Privacy (AD-5, AD-26)**: the owner's Listing resource includes the private fields (owner-only, `private, no-store` as today, `404` for any non-owner, administrator or Visitor exactly like the rest of the Draft); `PublicListingView.fields` (Story 2.12) must never contain a private-class field, and the 2.12 guard test must now have a REAL private field to bite on (add an assertion that `street`, `house_number`, `latitude`, `longitude` are absent from `public_listing`'s fields even though they are set). They also never appear in logs, error bodies, validation messages (messages are static), audit/revision rows, events (`listing.model_inputs_changed` carries no private values), openapi examples, or any URL. Add a canary test: set distinctive fake-word street/house values and coordinates, drive create/update/stale-409/validation-error/reads, and assert none of the values appear in the response of any non-owner path, in captured logs, in the revision `changes`, or in the 409 `current` of another account. The schema walk of AD-21/AD-26 (if present in the test suite) must still pass.
- **Wire**: flat registry ids in `fields` as for all other fields; `city` and `locality` are uuid strings; the registry response (`GET /api/v1/registry`) lists the new fields with `type: reference`, `reference`, and class so the frontend can render them generically; update the committed openapi and the freshness tests.
- **Docs/tests**: `docs/registry.md`, `docs/listings.md` (location + private address, the clear-locality rule), `docs/reference-data.md`, `docs/authorisation-matrix.md`; tests for every matrix row (including threaded concurrency of two writers, constraint/drift, the seed migration idempotency via MigrationExecutor, the locality endpoint, the cross-city locality, city change clearing locality, coordinate pairing, bounds and scale, contact-detail rejection, private-field privacy canaries, projection absence, revision rows without values, `fields.lock` freshness). Lessons from earlier stories apply (whole-body checks minus the request id, no vacuous assertions, rollback tests that can fail, static neutral messages, low-entropy fake words, `_reseed_flushed_policies`).

**Always (frontend, after the backend is merged and `npm run api:sync`):**
- A **Location** section on the Draft page, a generic registry-driven section like Details/Terms (`reference` type support added to the shared renderer, not a one-off): "Public location" group (City select from `/api/v1/reference/cities`, Locality select loaded for the chosen city and grouped "Areas" / "Societies", then Society, Phase, Sector, Block text inputs) and, visibly separated with its own heading, border and helper text, a "Private address (only you can see this)" group (Street, House number, Latitude, Longitude). Public group helper: "Shown to everyone on your listing." Private helper: "Never shown to buyers. Only you can see this." Changing City clears Locality in the form immediately (and mirrors the server rule), the Locality select is disabled until a city is chosen, reference loads show loading/empty/failed states with Retry, selects are real `<select>`s (or an accessible combobox) with labels, the saved values restore from the Listing resource. Saves use the same `base_revision` flow, conflict handling and per-field error mapping (codes `invalid`, `locality_not_in_city`, `contact_detail`, `too_long`, `out_of_range`, known codes only, never server text), no persistence of the private values anywhere (no localStorage/sessionStorage, no console output, no URL), fail-closed parsing of the reference lists, privacy test, axe clean at desktop and mobile in empty, filled, error and conflict states, keyboard operable, aria-disabled not disabled, README, stub backend updated (reference routes, validation mirroring, `locality_not_in_city`, clearing rule, private fields only in the owner resource), production-build check extended if a proxy or caching behaviour is relied on.

**Never:** expose any private-class field outside the owner's Draft resource; include private values in any log, event, revision row, audit row, error message or URL; store a locality that does not belong to the city; let a client choose a city or locality by name (uuids only); add maps, geocoding, distance or any external call; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Choose city and locality | valid pair, fresh `base_revision` | saved, revision +1 | N/A |
| Unknown / inactive / malformed city or locality id | any | `invalid` on that field, nothing written | envelope |
| Locality of another city | pair mismatch | `invalid` (`locality_not_in_city`) on `locality`, nothing written | envelope |
| Change city | new city with an old locality | locality cleared in the same write | N/A |
| Locality without city | locality only, no city saved | `invalid` on `locality` | envelope |
| Public text | society/phase/sector/block text, contact detail in it | stored trimmed / `contact_detail` | envelope |
| Private address | street, house number, coordinates | stored in the private table, owner-only | N/A |
| Coordinates | only one of the pair; out of range; too many decimals | `invalid`/`out_of_range` on the offending field | envelope |
| Clear a value | `null` | cleared; unanswered stays null, never 0/empty string | N/A |
| Another account / Visitor / administrator | reads the Draft | `404`, no private value anywhere | envelope |
| Public projection | published listing with private values set | projection carries no private-class field | guard test |
| Stale / concurrent | old `base_revision` | 409 `stale_revision` with `current` (owner only) | envelope |
| Locality list | known city / unknown city | items `{id,name,kind}` / `404` | envelope |
| Seed | migrate twice | no duplicates, edited rows untouched | test |

## Spec Change Log

## Review Triage Log

Backend half, as built (notes for the frontend half and reviewers):

- The migration is split: `catalog/0003_locality` (schema) and `catalog/0004_seed_karachi_localities` (data), not one `0003_seed_karachi_localities`.
- The contact-detail field error is the existing code `contains_contact_details` (not `contact_detail`). The frontend must map that code. The error envelope's `fields` carries the static message per field, so `locality_not_in_city` and `out_of_range` are told apart by their own messages.
- Revision rows record public values as before, and for private fields (or fields the registry does not define) only `true` (set) / `null` (cleared), never a value or just names. The spec's "field NAMES only" is read as that.
- `latitude`/`longitude` are the only decimals marked `signed: true`; `-0` is stored as `0.000000`.
- A stored city or locality that was deactivated later can be re-saved unchanged; only new values must be active. Ids are accepted in any common uuid form and stored canonical.
- `list_localities` returns at most 500 rows.

## Verification

**Backend:** `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check .; uv run ruff format --check .; env -u DJANGO_SETTINGS_MODULE uv run pytest -q; uv run python manage.py makemigrations --check --dry-run; uv run python manage.py spectacular --validate --file openapi.yaml` (openapi regenerated deliberately).
**Frontend:** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && npm run check:prod-admin && npm run check:prod-listings && npm run check:prod-profile && PW_CHANNEL=chrome npx playwright test` (full run in the background, once; never two at once).
