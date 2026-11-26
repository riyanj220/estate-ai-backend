---
title: 'Story 2.19: Correction window for locality and area (backend half, then frontend half)'
type: 'feature'
created: '2026-11-26'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Story 2.19)'
  - '{project-root}/_bmad-output/planning-artifacts/prds/prd-estate-ai-backend-2026-11-18/prd.md (identity fields, B17, B24)'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-7)'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-18-republication.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/testing.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Identity fields are locked while a Listing is Published, so an honest typo in the locality or area forces an archive and possibly another quota slot.

**Approach:** Backend: a one-time correction of locality and area within 24 hours of the last counted Publication, written by `listings.publication`, recorded as a revision and as `correction_used` on that counted event, with the event's fingerprint updated so no slot is ever charged for it; an eligibility read for the client. Also remove the marla basis from the identity set (Story 2.17 added it by mistake; the epic and PRD say it is not an identity field). Frontend: a correction action with its remaining time on a recently published Listing. Backend first and merged, then frontend. Workflow: narrow local runs (`docs/testing.md`), full suites in CI; decide ambiguities yourself with the best option and report them.

## Boundaries & Constraints

**Always (backend):**
- **Identity set**: the registry `quota_identity` flag covers exactly purpose, category, subtype, city, locality, area_value, area_unit (remove marla_basis; society, phase, sector, block stay unflagged). Registry version bump, `fields.lock`, and a data migration that recomputes stored event fingerprints with `apps/listings/identity_migration.recompute_fingerprints` (old set includes marla_basis, new set does not; its drift rule applies), tested through `MigrationExecutor`. marla_basis is then freely editable on a Published Listing under the ordinary edit rules; update docs, copy (OpenAPI descriptions) and tests that listed eight fields.
- **Correctable fields**: locality, area_value, area_unit, and marla_basis only as the companion needed for a valid area (accept it in the correction body). Never category, subtype, purpose or city (any other key in the body: `400 validation_error`, nothing written).
- **Window**: the Listing is Published and not restricted, and its last COUNTED event (kinds initial, republish_counted; seed and free events never) has `now < created_at + 24 h` and `correction_used` false. Exactly 24 h after is closed. `now` is read after the account lock.
- **`publication.correct(actor, listing_id, base_revision, changes, now=None)`** (only `publication.py` writes events): one transaction: lock account then Listing (owner only; non-owner, admin, unknown 404; Visitor 401); compare `base_revision` (409 stale_revision with `current`); status must be published (409 conflict) and not restricted (403 forbidden); account state: the same accounts that may edit a Published Listing today (Suspended and Deactivated refused 403); window open and unused, else `409` with a new error code `correction_unavailable` (static message: the correction window is closed; archive the listing to change these details); validate the changes with the same validation core as a Published edit (locality must belong to the listing's city; area rules; completeness on touched fields), all field errors at once, nothing written; a request that changes nothing is `400 validation_error` and does NOT use up the correction; write the fields through the existing single write path (revision +1, revision row, provenance manual), set `correction_used` true and `identity_fingerprint` to the new fingerprint on that counted event, audit `listing.corrected` (ids and field names only), emit `listing.model_inputs_changed` when a valuation_input field changed (after commit). No new event row, no quota consumed or checked. A later same-month republish with unchanged identity is therefore free (test).
- **Events table privileges**: the runtime role keeps INSERT and SELECT only, plus a column-level UPDATE on exactly `correction_used` and `identity_fingerprint` (extend the privileges hook; test that any other column UPDATE, DELETE and TRUNCATE are still refused). Update the AST write guards accordingly (only `publication.py` may update events, only these two columns).
- **Endpoints**: `GET /api/v1/listings/{id}/correction` → `{"available": bool, "expires_at": iso8601|null}` (owner only, no-store; `available` false with null when there is no open window for any reason; `expires_at` in the Karachi offset like the allowance); `POST /api/v1/listings/{id}/correction` body `{"base_revision": int, "fields": {...}}`, CSRF, owner, no-store, `200` owner Listing resource. Regenerate `openapi.yaml` and the authz matrix; update `docs/listings.md`, `docs/account-states.md` if needed.
- **Concurrency and failure**: two simultaneous corrections: exactly one succeeds; a correction racing archive/status change: one wins cleanly; injected failures leave fields, flag, fingerprint and audit untouched.
- **Tests**: every matrix row, the 24 h boundary (one second before, exactly), second correction, correction after republish (a new counted republication opens a new window; a free republish does not reopen a used one), seed listing (no window), locality of another city, area validation, field whitelist, quota allowance unchanged, fingerprint updated, next republish free, privileges, migration. Mutation-check the window boundary, the used flag, the counted-event selection and the field whitelist.

**Always (frontend, after the backend is merged and `npm run api:sync`):**
- On a Published Listing page, when the eligibility read says available: a "Correct locality or area" section showing until when (Karachi time) and roughly how long is left, with a form (locality select for the listing's city, area value, unit and marla basis where applicable, prefilled), only changed fields sent, a confirmation dialog stating it can be done only once, success announcement and refreshed page, field errors next to fields, stale/conflict/`correction_unavailable` handling (re-read eligibility), locks like the other actions, disabled with a reason while the main form has unsaved changes. When not available: a short note "To change the locality or area, archive this listing and republish it." Stub, specs with axe at desktop and mobile, README. Update the republish copy so it no longer names the marla basis. Marla basis becomes editable on Published pages automatically through the registry flag.

**Never:** consume or check quota for a correction; allow a second correction on the same counted event; correct city, category, subtype or purpose; write events outside `publication.py`; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Correct within window | published, counted event < 24 h, unused | saved, revision +1, flag set, fingerprint updated, no slot | N/A |
| Second correction / after 24 h / no counted event | published | `409 correction_unavailable` | envelope |
| City, category, subtype, purpose or other key | body | `400`, nothing written | envelope |
| Invalid locality or area | body | `400` field errors, window not used | envelope |
| No actual change | same values | `400`, window not used | envelope |
| Not published / restricted | any | `409 conflict` / `403` | envelope |
| Suspended | account | `403`, nothing written | envelope |
| Stale revision | old base | `409 stale_revision` + current | envelope |
| Other account / admin / Visitor | any | `404` / `404` / `401` | envelope |
| Marla basis edit on Published | ordinary PATCH | allowed, no correction needed | N/A |

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

## Verification

**Backend:** `uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run lint-imports && uv run python manage.py makemigrations --check --dry-run && uv run python manage.py spectacular --validate --file openapi.yaml`, `scripts/test-changed.sh`, and one parallel full run `env -u DJANGO_SETTINGS_MODULE uv run pytest -n 3 -q` before the merge.
**Frontend:** lint, typecheck, build, api:check, prod checks, the new and changed specs, the Listing-page specs touched, `npm run test:e2e:changed`; the full suite runs in CI.
