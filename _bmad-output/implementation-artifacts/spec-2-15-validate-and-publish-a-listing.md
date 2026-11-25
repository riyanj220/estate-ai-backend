---
title: 'Story 2.15: Validate and publish a Listing (backend half, then frontend half)'
type: 'feature'
created: '2026-11-25'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Story 2.15)'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-5, AD-7, AD-17, AD-18, AD-22)'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-14-publication-quota-policy-and-allowance.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-12-visibility-policy-and-public-projections.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/registry.md'
  - '{project-root}/docs/testing.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Draft can be built and previewed but never published. Publishing must be immediate (no approval), valid, within the monthly allowance, safe against concurrency and retries, and all-or-nothing.

**Approach:** Backend: `listings.publication.publish` (the only code that sets `published`, AD-7) in one transaction: claim the idempotency key, lock the account then the listing, compare `base_revision`, validate required data and unconfirmed AI values, check account state and quota, write a counted `initial` `PublicationEvent` with the identity fingerprint, set the status, write the audit record; emit `listing.published` after commit. A `POST /api/v1/listings/{id}/publish` endpoint. Frontend: a Publish action on the Draft page with field-level errors and an updated allowance. Backend first and merged, then frontend. Workflow: narrow local runs (`docs/testing.md`), full suites in CI; decide ambiguities yourself with the best option and report them.

## Boundaries & Constraints

**Always (backend):**
- **Module and guard**: new `apps/listings/publication.py` with `publish(actor, listing_id, base_revision, idempotency_key, now=None)` (actor first, AD-22). It is the only code that sets `ListingStatus.PUBLISHED`; the existing status-setting path (`set_status` or the equivalent in `lifecycle`/`services`) must reject `published` (add it if absent) and an architecture guard test must fail if any other module assigns `status = published` or writes `PublicationEvent`. Update the 2.14 AST write-guard test to allow exactly `listings/publication.py` to create `PublicationEvent`.
- **Transaction order (AD-7)**: one `transaction.atomic`; (1) validate the key shape (header `Idempotency-Key`, 1..128 chars `[A-Za-z0-9._:-]`, required: missing/invalid is `400 validation_error` on the header, not a 500) and claim it through `kernel.idempotency` with scope `listings.publish`, account id, and request hash = SHA-256 over (listing id, base_revision); a replay inside 24 hours returns the original result (the Listing as it is now, same `200` shape) with no second charge, no second event, no second audit; the same key with a different hash is `422 idempotency_key_reused`; (2) `identity.lock_account`, then lock the listing row owned by the actor (non-owner/admin/missing/Visitor: the same 404/401 as the other owner routes); (3) compare `base_revision` (stale: `409 stale_revision` with the owner's `current`); (4) status must be `draft` (else `409 conflict`, nothing written); (5) account state: only Active may publish; Email-unverified is refused `403` with its own static reason code, Suspended with another (distinct, neutral, no account details), Deactivated has no session; (6) validate (below); (7) quota: `quota.allowance_for(...)` (2.14) under the account lock with `now`; `remaining < 1` is refused with `409 quota_exhausted` carrying the caller's own `limit`, `used` and `resets_at` in the error details (offset-aware ISO strings) and a static message; no write happens; (8) write `PublicationEvent(kind=initial, counted=True, identity_fingerprint, idempotency_key, listing_id, account_id)`; (9) set the status to published and bump `revision` (add a revision row recording the status change without content); (10) write the audit record `listing.published` (ids only); (11) `claim.complete("listing:<id>")`; (12) `on_commit` emit `listing.published` through `kernel.events` (frozen event with listing id and account id only, schema-registered; no subscriber required, nothing happens when nobody subscribes). No AI call, upload, email or notification inside the transaction. Any refusal or exception leaves no quota consumed, no event row, no audit row, no idempotency claim (the claim rolls back with the transaction), no status change; a failing step injected mid-transaction (patch the audit write, the event write and the status write in turn) leaves nothing half-applied (NFR-4 fault-injection tests for each).
- **Validation (field-level errors, all at once, owner-only)**: collect every problem and answer `400 validation_error` with `fields` naming each (the existing envelope; use the existing code `required` for missing items). Required: purpose, category, subtype (all fixed at creation, still checked), title, asking price with price basis (and rental period when the purpose is rent), city and locality (and that the locality belongs to the city), area with unit, and for House and Apartment bedrooms and bathrooms; derive the list from the registry (`requirement: required_for_publication` plus applicability) so a registry change cannot drift, and make sure the spec list above is exactly what the registry yields (adjust the registry only if a required item here is missing there, with a version bump and `fields.lock` update, and say so). At least one image: error keyed `images` with a new static sentence ("Add at least one image."; add it to `FIELD_MESSAGES` and report the new sentences, the frontend mirrors them). Existing per-field validity rules still apply to stored values. AI-supplied unconfirmed values: if any field flagged `quota_identity` or of class `private` has provenance `origin=ai` and `confirmed=false`, refuse naming that field with a new static code/sentence (for example `confirm_required`: "Confirm this value before publishing.") (testable with a system-actor fixture that writes an ai value). Missing optional data never blocks.
- **Fingerprint**: SHA-256 hex over the canonical JSON (sorted keys, stable value encoding, decimals normalised) of the registry fields flagged `quota_identity`, computed in one place (`publication.identity_fingerprint(view)`), documented in `docs/listings.md`; it is stored on the event and never exposed.
- **Concurrency**: 20 simultaneous requests (threads, separate connections, distinct or same keys) against one remaining slot: exactly one succeeds, the rest get `409 quota_exhausted`/`409 conflict`/idempotent replay as appropriate, exactly one event row; the same key replayed concurrently charges once. Two different drafts of the same account racing for one slot: one wins.
- **Endpoint**: `POST /api/v1/listings/{id}/publish`, body `{"base_revision": int}`, header `Idempotency-Key`, session + CSRF like the other writes, owner only, `Cache-Control: no-store`, `200` with the owner Listing resource (`status: published`). Documented responses: 200, 400, 401, 403, 404, 409 (stale/conflict/quota), 422. Update the `docs/authorisation-matrix.md` route table (regenerated), `docs/listings.md` (publication, idempotency, fingerprint, quota), `docs/visibility.md` (a published listing becomes public under the 2.12 policy; verify with a test that `public_listing` now returns it for an Active owner and not for an Email-unverified owner), regenerate `openapi.yaml`. The owner Listing resource and `PublicListingView` need no shape change.
- **Tests (narrow runs)**: every matrix row; the happy path end to end (status, visibility, revision bump, event row with fingerprint, audit row, domain event emitted after commit and not before, public projection reachable); each missing-field case and the all-at-once error set; the image requirement; rental period; bedrooms/bathrooms; locality/city mismatch; unconfirmed AI value; stale revision; non-draft; wrong owner/admin/Visitor; Email-unverified and Suspended refusals; quota exhausted (details carry limit/reset, nothing consumed); policy change effective-dating (a later higher limit allows it); month boundary (an event from last Karachi month does not count); idempotency (replay returns the same body and charges once, different content 422, missing/invalid header 400, key scoped per account); concurrency tests above; fault-injection tests; the `set_status` rejection and the single-writer architecture guard; no AI/email/upload call inside the transaction (assert via patches that nothing external is called). Lessons from earlier stories apply (static messages, whole-body checks, low-entropy fake words, mutation-check the quota and idempotency branches).

**Always (frontend, after the backend is merged and `npm run api:sync`):**
- A **Publish** section on the Draft page (only for `draft`): short copy ("Publishing makes this listing visible to everyone immediately. It uses 1 of your monthly publications."), the current remaining count from the allowance route, and a Publish button (aria-disabled while a save/upload is in flight or while the form has unsaved changes, with the reason shown: "Save your changes before publishing"). Clicking sends `POST .../publish` with CSRF, `base_revision`, and an `Idempotency-Key` generated per attempt and reused for retries of the same attempt (a network error or timeout retries with the same key; a new attempt after any edit gets a new key). States: publishing (locks save/images/delete like the delete flow), success (page switches to the published state: status shown, read-only note, success announcement, remaining allowance updated by re-reading the route), validation errors (a summary at the top that lists every item with links to the field, errors next to each field using the existing sections' error display, the images error next to Images, the confirm-required error next to the field), quota exhausted ("You have used all N publications this month. The allowance resets on <date>." with a link to the dashboard, using the Karachi formatter from 2.14), Email-unverified ("Verify your email to publish." with a link to the verification flow) and Suspended ("Your account cannot publish right now.") messages, stale revision (existing conflict flow), already published/409 conflict (refetch), 422 key reuse (new key, retry once), network failure with Retry. Update `error-sentences.ts` and `tests/e2e/field-sentences.json` for the new backend sentences (the drift test must stay green). The Draft page must not offer Publish for non-drafts. Stub backend: the publish route mirroring every rule above (required-field computation from the stub's own draft data, quota counter per account, idempotency replay/422, account states), spec file with axe at desktop and mobile in the key states, README, prod check only if a proxy behaviour is relied on. Existing specs that visit the Draft page must still pass (run them: listings, listing-edit, listing-images, listing-preview-delete, listing-location, listing-amenities, listing-terms).

**Never:** consume quota on a refused or failed attempt; publish through any path other than `publication.publish`; do AI/email/upload work inside the transaction; echo field values in errors; expose the fingerprint; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid Draft, allowance left | owner POST with fresh revision and key | `200`, published, one counted `initial` event, audit, event after commit | N/A |
| Missing required items | draft lacks title/price/city/image/... | `400` listing every item, nothing consumed, still a Draft | envelope |
| Unconfirmed AI sensitive value | ai + unconfirmed field | `400` naming the field | envelope |
| Quota exhausted | remaining 0 | `409 quota_exhausted` with limit and reset date, nothing consumed | envelope |
| Same key replay | same key and content | original result, no second charge | N/A |
| Same key, other content | different revision/listing | `422 idempotency_key_reused` | envelope |
| Key missing or malformed | no/invalid header | `400` | envelope |
| 20 concurrent, one slot | threads | exactly one succeeds | N/A |
| Stale revision | old `base_revision` | `409 stale_revision` + current | envelope |
| Not a draft | published | `409 conflict` | envelope |
| Email-unverified / Suspended | account state | `403` with distinct neutral reason | envelope |
| Other account / admin / Visitor | any | `404` / `404` / `401` | envelope |
| Fault mid-transaction | injected error | nothing half-applied | test |

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

### Backend (round 1)

Decisions accepted: the request checks run before the transaction (Visitor 401, not-owner 404, shape 400); the account state check follows the revision and Draft checks; replay returns the owner view as it is now; no `set_status` path exists (an AST guard covers the single writer); fingerprint covers today's `quota_identity` fields (purpose, category, subtype).

Fixes from review: (1) images count only live links (`selectors.live_image_count`, shared with the image cap); (2) the unconfirmed-AI guard is `origin == ai and confirmed is not True` on raw provenance, only for fields that have a value and apply; (3) the key is looked up against `PublicationEvent` right after the claim and replays the original success after 24 hours or a purge (same Listing), `422` for another Listing, `not_found` for a gone Listing or malformed reference, and the `result_ref is None` branch is tested; (4) `now` is read after `lock_account` unless passed; (5) every present stored value is re-judged with the cross-field rules (a real defect found: stored amenities arrive as a mapping proxy and needed a plain dict), and a rich valid draft of every Subtype publishes; (6) no backend withdrawal of an already delivered `listing.draft_expiring` notice (documented); (7) deterministic fingerprint canonicaliser with a golden vector; (8) refusal-precedence, lock-before-allowance, no-external-call and import guard tests, whole-body 404 comparison, fingerprint never in a body, audit or event; (9) the single-writer guard flags `**kwargs`, computed `setattr`, `bulk_update` of status and raw SQL on the Listing table.

Accepted deferrals: the fingerprint covers only today's `quota_identity` fields; `QuotaPolicyMissing` stays a 500; no new field sentences or error codes in this round.

## Verification

**Backend (narrow while developing, full in CI):** `uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run lint-imports && uv run python manage.py makemigrations --check --dry-run && uv run python manage.py spectacular --validate --file openapi.yaml`, then `scripts/test-changed.sh` and, before the merge, one parallel full run `env -u DJANGO_SETTINGS_MODULE uv run pytest -n 3 -q`.
**Frontend:** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && npm run check:prod-admin && npm run check:prod-listings && npm run check:prod-profile`, the new and changed specs, the Draft-page specs listed above, and `npm run test:e2e:changed`; the full suite runs in CI.
