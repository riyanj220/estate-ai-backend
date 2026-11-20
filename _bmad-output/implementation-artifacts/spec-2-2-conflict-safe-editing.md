---
title: 'Story 2.2: Conflict-safe editing with title, description and price (backend half)'
type: 'feature'
created: '2026-11-20'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-1-start-a-draft.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/registry.md'
  - '{project-root}/docs/story-checklist.md'
  - '{project-root}/docs/privacy-patterns.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Draft exists but cannot be edited: there is no way to save a title, description or price, two sessions could silently overwrite each other, and nothing records where a field's value came from.

**Approach:** Add the single write path for Listing field changes, `listings.services.apply_updates(actor, listing_id, base_revision, updates, origin)`, with revision-checked saving (a stale `base_revision` is `409` with the current data and nothing is overwritten), registry-driven validation of the first editable fields (title, description, Asking price, price basis, rental period), per-field provenance set by the service, a revision record for every change, the contact-detail check on text, and the `listing.model_inputs_changed` event machinery. Exposed to owners as `PATCH /api/v1/listings/{id}` with `{base_revision, fields}`. This spec is the backend half; the Draft editing form is a second spec after the backend merges.

## Boundaries & Constraints

**Always:**
- **Registry additions (bump `version` to 2 and update the lock with `scripts/registry_lock.py`)**: the loader gains the field types `text` (with `min_length`/`max_length`) and `money` (with `min`/`max`, currency PKR) alongside `enum`. New fields: `title` (text, 10 to 100 characters after trimming and whitespace collapse, required for publication, public, `ai_extractable`), `description` (text, up to 5000 characters, recommended, public, `ai_extractable`), `asking_price` (money, greater than 0 and at most 999,999,999,999.99 with at most 2 decimals, required for publication, public, NOT `valuation_input`, `ai_extractable`), `price_basis` (enum `total`, `per_unit`, `rental_period`; required for publication; `sale` allows `total` and `per_unit`, `rent` allows only `rental_period`), `rental_period` (enum `day`, `week`, `month`, `year`, never assumed; applies only to purpose `rent`, required for publication when rent). Encode the purpose rules in the registry (`applicability` and an `allowed_values_by_purpose` map for `price_basis`) and enforce them from it. Flags per the architecture rules; none of these is private-class. All earlier registry rules, bad-YAML tests and the lock test keep working with the new types.
- **Storage**: `title` and `description` become nullable columns on `PropertyListing` (empty is `null`); price data lives in a new `ListingTerms` model (one-to-one with the listing inside `listings`, created lazily by `apply_updates` and by `create_draft` going forward, with `asking_price` NUMERIC(14,2) nullable, `price_basis`, `rental_period` nullable with CHECK constraints generated from the registry like the 2.1 ones, `updated_at`); provenance is a JSON column `provenance` on `PropertyListing` mapping registry field id to `{"origin": "manual"|"ai", "confirmed": bool}`; migrations unmerged-safe (one new migration for this story). Money is NUMERIC, never float, and is serialised as a decimal STRING with two decimals (`"1500000.00"`).
- **`apply_updates(actor, listing_id, base_revision, updates, origin="manual") -> ListingView`** (actor first): the ONLY function that changes Listing fields after creation. Order of decisions: Visitor `unauthenticated`; the listing id must be a canonical UUID owned by the actor's account, otherwise `not_found` for everyone else (another owner, administrator, system actor without ownership, unknown) with no confirmation of existence; the actor must hold `edit_drafts` (`forbidden` for Suspended, Deactivated, administrators; evaluated AFTER the not-found rule); `base_revision` must be a positive integer (`validation_error` otherwise) and `updates` a non-empty mapping of registry field ids (unknown ids, ids that this story does not make editable such as `purpose`, `category`, `subtype` or later fields, non-mapping bodies: `validation_error` with `unknown_field`/`not_editable` codes, all failing fields at once, values never echoed). Then, in ONE transaction that locks only the listing row (`select_for_update`): compare the stored `revision` with `base_revision`; a mismatch raises a `StaleRevision` domain error carrying the current `ListingView` and writes nothing; otherwise validate every update against the CURRENT registry (the listing's `registry_version` is advanced to the current version for editable listings so a Draft created under an older registry is revalidated; published Listings are not re-versioned or altered by registry changes), apply it, set provenance for each changed field, increment `revision` by exactly one, write a `ListingRevision` row (`revision`, `origin`, `actor_account_id`, `changes` JSON of the field ids and their new values as strings or null), and return the new view. An update that changes nothing (every value equal to the stored one) increments nothing and writes nothing, and returns the current view. Saving never requires complete data; a field set to `null` clears it (except fields the registry marks non-nullable: none today); cross-field rules (a `rental_period` on a sale, `price_basis` incompatible with the purpose, `price_basis` `rental_period` without a rental period when rent) are evaluated against the resulting state and reported as field-level codes (`not_applicable`, `invalid_choice`, `required_for_rent`), never as a server error. The same rule applies to edits of a Published listing and to simultaneous sessions (the status gate is a code-defined set `EDITABLE_STATUSES` that holds only `draft` today; document that Story 2.15 extends it).
- **Provenance**: `origin` and `confirmed` are set by the service from the actor and the call path: the `origin` argument is chosen by the calling layer (the owner API passes `manual`; the future Assistant passes `ai`) and is NEVER read from a client payload or model output; a field written with origin `manual` is stored `confirmed: true`; a field written with origin `ai` is stored `confirmed: false` until the owner confirms it (confirmation arrives in a later story). The wire shape exposes `provenance` as a map from field id to `{origin, confirmed}` for set fields only.
- **Text rules**: `title` and `description` are trimmed, internal whitespace collapsed (a description keeps paragraph breaks as single newlines, at most two in a row), NFC-normalised, refused when they contain control or invisible characters (inner ZWNJ/ZWJ allowed), and passed through `kernel.privacy.patterns.reject_contact_details`: phone numbers, email addresses and street addresses are refused with the field code `contains_contact_details` and an explanatory message in the static catalogue ("Titles and descriptions cannot contain phone numbers, email addresses or street addresses."); prices, areas and counts in legitimate text ("3 bedrooms, 120 sq yd, Rs 2.5 crore") are NOT rejected (a false-positive test set of at least 15 realistic strings, including 7-digit prices written with separators and area figures). The input size is bounded before any normalisation (reject over 20,000 raw characters as `too_long` without scanning).
- **Event `listing.model_inputs_changed`**: `listings/events.py` defines the frozen event `ListingModelInputsChanged(listing_id)`; `apply_updates` emits it through the kernel event bus (after commit) when a field flagged `valuation_input` in the registry actually changed; with no subscribers emitting is a no-op. No field is flagged `valuation_input` yet, so the behaviour is proven with a registry fixture in tests (monkeypatch the registry accessor, not the YAML).
- **Revision and audit**: every successful change writes exactly one `ListingRevision`; edits are recorded by the revision rows (no extra audit action), documented in `docs/listings.md`; a failure after the write rolls everything back (listing, terms, revision, event).
- **API**: `PATCH /api/v1/listings/{id}` (signed in, CSRF enforced, `no-store`), body strict `{base_revision, fields}` (any other top-level key is `unknown_field`), calls `apply_updates(..., origin="manual")` with the origin fixed in the view. `200` returns the listing resource. `409` is the standard error envelope with code `stale_revision` (already in the catalogue) AND the current listing resource under a top-level `current` key so the client can reload and reapply; document it with a dedicated OpenAPI response schema and assert it in a contract test. `400` carries field-level codes keyed by registry field ids; `404` for non-owners (never `403`); `403` for the capability refusal. The listing resource gains `provenance` and the new fields in `fields` (`title`, `description`, `asking_price` as a string, `price_basis`, `rental_period`, each `null` when unknown); update the 2.1 wire-shape contract test accordingly (AD-24 permits additions; `owner_account_id` and internal columns still never appear). The own-list items keep the same shape.
- **Idempotency and replay**: retrying the same PATCH with the same `base_revision` after it succeeded is a `409` (the revision moved), never a double write; documented as the client's cue to reload.
- **Docs**: update `docs/listings.md` (the update path, revision rules, provenance, text rules and false-positive policy, the 409 body, `EDITABLE_STATUSES`), `docs/registry.md` (new types and fields, `allowed_values_by_purpose`, version 2), `docs/privacy-patterns.md` (listing text now uses the shared patterns) and `README.md`; `openapi.yaml` and `docs/authorisation-matrix.md` regenerated. All earlier tests, convention tests and import-linter contracts keep passing (the 2.1 tests that asserted the exact `fields` keys are updated for the additions only).

**Never:** let a client choose `origin` or `confirmed`; write a Listing field outside `apply_updates`; compare revisions after validation (the stale check comes first, before any other work on the data); echo submitted values in errors; accept floats for money; return `owner_account_id`; confirm the existence of another owner's listing (404 for everything not owned); alter a published Listing because the registry version moved; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Save title, description, price | owner, fresh `base_revision`, valid fields | 200 listing: revision +1, values stored, price as `"1500000.00"`, provenance `manual/confirmed` for each changed field, one `ListingRevision` | N/A |
| Partial save | only `title` | only that field changes; others untouched; no completeness required | N/A |
| Clear a field | `description: null` | stored null, provenance entry removed, revision +1 | N/A |
| No change | all values equal stored | 200 current listing, revision unchanged, no revision row | N/A |
| Stale revision | `base_revision` older than stored | 409 `stale_revision` with `current` listing; nothing written | envelope + current |
| Replay | same PATCH sent twice | second is 409 | envelope + current |
| Concurrent edits | two sessions with the same base | exactly one 200, the other 409 (threaded test) | N/A |
| Rent terms | purpose rent, `price_basis: rental_period`, `rental_period: month` | accepted | N/A |
| Sale with rental period | sale + `rental_period` | field code `not_applicable` on `rental_period` | envelope |
| Basis/purpose mismatch | sale + `rental_period` basis, rent + `total` | `invalid_choice` on `price_basis` | envelope |
| Rent without period | rent, basis `rental_period`, no period (resulting state) | `required_for_rent` on `rental_period` | envelope |
| Money rules | zero, negative, 3 decimals, float JSON number, string with letters, over the maximum | `invalid_price` / `invalid` codes on `asking_price`; stored as NUMERIC | envelope |
| Text rules | phone number, email, street address in title or description | `contains_contact_details` with the explanation; value not echoed | envelope |
| False positives | at least 15 realistic strings with prices, areas, counts | accepted | test |
| Text shape | control characters, 20,001 raw characters, 9-character title | `invalid`/`too_long`/`too_short` codes | envelope |
| Unknown or non-editable field | `purpose`, `category`, `subtype`, `owner_account_id`, `status`, unknown ids | `unknown_field` / `not_editable`; nothing written | envelope |
| Bad body | non-mapping `fields`, empty `fields`, missing or non-integer `base_revision`, extra top-level key | 400 validation error | envelope |
| Older registry | Draft created under version 1 | revalidated against the current registry, `registry_version` advanced | test |
| Other owner / admin / Visitor / unknown id | any | 404 (Visitor: 401); never 403 for ownership | envelope |
| Suspended / deactivated owner | session with `edit_drafts` denied | 403 `forbidden`; nothing written | envelope |
| No CSRF | PATCH without a token | 403 | envelope |
| Provenance source | request body carries `origin` or `confirmed` | refused (`unknown_field`); service argument decides; `origin="ai"` writes `confirmed: false` | test |
| Model-input event | a `valuation_input` field changes (registry fixture) | `listing.model_inputs_changed` emitted after commit; none for other fields; no-op without subscribers | test |
| Rollback | failure after the listing write | nothing written, no event | test |
| Privacy | logs, errors, revision rows | no contact values echoed, no owner id in responses | canary test |
| Contract | `openapi.yaml` | PATCH route, 409 schema with `current`, new fields, freshness passes | test |

</frozen-after-approval>

## Code Map

- `apps/catalog/registry.py`, `registry/fields.yaml`, `registry/fields.lock` (version 2, new types and fields), `scripts/registry_lock.py`
- `apps/listings/models.py`, `migrations/0002_editing.py` (`title`, `description`, `provenance` on `PropertyListing`, `ListingTerms`), `services.py` (`apply_updates`, `StaleRevision`, text and money validators), `selectors.py` (`ListingView` and `view_of` additions), `events.py`, `lifecycle.py` (`EDITABLE_STATUSES`)
- `apps/kernel/errors.py` (field messages for the new codes), `apps/presentation/api/listings.py` (PATCH view, 409 body), `config/urls.py` if needed
- `openapi.yaml`, `docs/authorisation-matrix.md` regenerated
- `tests/catalog/test_registry.py` (types), `tests/listings/test_apply_updates.py`, `tests/listings/test_text_rules.py` (false-positive set), `tests/api/test_listings_api.py` (PATCH, 409, contract)
- `docs/listings.md`, `docs/registry.md`, `docs/privacy-patterns.md`, `README.md`

## Tasks & Acceptance

**Execution:**
- [ ] registry types and fields (version 2, lock), loader tests
- [ ] models and migration, `apply_updates` with the stale check, provenance, revision rows
- [ ] text and money validators, the contact-detail check and its false-positive set
- [ ] the model-inputs event, PATCH endpoint with the 409 body, contract regeneration
- [ ] tests for every matrix row; docs

**Acceptance Criteria:**
- Given a Draft I own, when I send `{base_revision, fields}` with title, description, Asking price, price basis and, for rent, a rental period, then `apply_updates` validates against the registry, saves, increments the revision and writes a revision record with origin manual, saving never requires complete data, and money is NUMERIC and serialised as a string in PKR.
- Given my update carries an older `base_revision`, when I submit it, then the API returns 409 with the current data and nothing is overwritten, and the same rule applies to edits of a Published listing and simultaneous sessions.
- Given a Draft created under an earlier registry version, when I next edit it, then it is revalidated against the current registry and published Listings are not altered.
- Given another account or a Visitor, when it requests or edits my Draft, then the API returns not-found.
- Given a saved field, when its provenance is stored, then each field carries its origin and a confirmed flag, both set by the service from the actor and call path, never from the client payload.
- Given a field flagged `valuation_input` changes, when the edit commits, then `listing.model_inputs_changed` is emitted through the kernel event bus and emitting with no subscribers is a no-op.
- Given a title or description, when I save it, then the contact-detail check rejects phone numbers and addresses with an explanation, and prices, areas and counts in legitimate text are not rejected.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Design Notes

Decisions made without asking: the stale check comes before any validation so a conflicting client always learns about the conflict first; the service, not the client, decides provenance (the API fixes `origin` to manual, the Assistant will pass `ai`); price data lives in `ListingTerms` as the architecture says while title and description are columns on the listing; a no-op edit does not consume a revision so retries stay cheap and the history stays meaningful; rent always uses the rental-period basis and the period is never assumed to be monthly (PRD addendum); the 409 body carries the current listing so the client can reload without a second request; ownership failures are always not-found so existence is never confirmed; `EDITABLE_STATUSES` is a code-defined set so Story 2.15 widens it deliberately.

## Verification

**Commands (backend):**
- `uv run python manage.py migrate --database=migrator`
- `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run pytest -q && uv run python manage.py makemigrations --check --dry-run`
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- regenerate and commit; `uv run python scripts/registry_lock.py` after the YAML change
