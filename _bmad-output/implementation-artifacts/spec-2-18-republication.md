---
title: 'Story 2.18: Republication (backend half, then frontend half)'
type: 'feature'
created: '2026-11-25'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Story 2.18)'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-7)'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-15-validate-and-publish-a-listing.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-17-manage-the-listing-lifecycle.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-14-publication-quota-policy-and-allowance.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/testing.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An archived Listing can never come back, and relisting must not let an owner dodge the monthly quota.

**Approach:** Backend: `listings.publication.republish` (same transaction and guarantees as `publish`, AD-7) that takes an Archived Listing back to Published, revalidates it fully, and decides between a counted event (`republish_counted`, uses quota) and a free one (`republish_free`) from the identity fingerprint and the Karachi calendar month of the last counted event; plus `POST /api/v1/listings/{id}/republish`. Frontend: a Republish action on the Archived Listing page that shows the outcome (counted or free) and the updated allowance. Backend first and merged, then frontend. Workflow: narrow local runs (`docs/testing.md`), full suites in CI; decide ambiguities yourself with the best option and report them.

## Boundaries & Constraints

**Always (backend):**
- **`publication.republish(actor, listing_id, base_revision, idempotency_key, now=None)`** in `apps/listings/publication.py` (the only module allowed to set `published` and to write `PublicationEvent`, AD-7 guard unchanged; share the transaction skeleton with `publish`, do not copy it: refactor the common steps into one internal function parameterised by mode). Same order and guarantees as `publish`: shape checks before the transaction (Visitor 401, non-owner/admin/unknown/malformed 404, header and body shape 400); inside one atomic block: claim the idempotency key (scope `listings.republish`, hash over listing id and base_revision, replay returns the original result without a second charge or event, same key with different content `422 idempotency_key_reused`, expired-claim replay from the event exactly as for publish), `identity.lock_account` then lock the Listing, read `now` after the lock, compare `base_revision` (`409 stale_revision` with `current`), status must be `archived` (a `sold` or `rented` Listing must first be returned to archived: `409 conflict`; `draft`/`published`/`removed`: `409 conflict`, removed also never republishable), a `restricted` Listing is refused `403 forbidden` (nothing written), account state: only Active may republish (Email-unverified `403 email_not_verified`, Suspended `403 publishing_unavailable`, same codes as publish), full revalidation through the SAME completeness core as publication with the FULL re-judge of every stored value (not the touched-only rule of Published edits, because Archived edits were only validated field by field): every required item, images (live >= 1), city/locality consistency, unconfirmed AI sensitive values, all reported at once as `400 validation_error`; then the quota decision below; write the event, set `published`, bump `revision` with a revision row, audit `listing.republished` (ids and the event kind only), emit `listing.published` after commit (same event type as publish; add a field or a sibling event only if consumers need to tell them apart: decide), `claim.complete`. Any refusal or failure leaves no quota consumed, no event, no audit, no claim, no status change (fault-injection tests as for publish).
- **Counted or free (the rule, exactly)**: compute `identity_fingerprint(view)` of the Listing as it is now. Find the Listing's last COUNTED event (kinds `initial` and `republish_counted`; `seed` and `republish_free` events are never baselines). The event is `republish_free` only if ALL hold: a last counted event exists, its `created_at` falls in the same Karachi calendar month as `now`, and its fingerprint equals the current fingerprint. Otherwise it is `republish_counted` (no baseline, a different month, or a changed identity field). A counted republish needs `allowance.remaining >= 1` under the account lock (else `409 quota_exhausted` with the same `details`, nothing consumed); a free one needs no remaining allowance and consumes none. The event row: `kind`, `counted` (true only for `republish_counted`, by the existing CHECK), `identity_fingerprint` (always stored), `idempotency_key`, `listing_id`, `account_id`. A counted republish becomes the new baseline; a free one does not move it. Boundary tests: the same Karachi month vs the first second of the next month (UTC and Karachi differ by 5 hours), a changed identity field (each of the eight identity fields), a changed non-identity field (free), a seeded listing with no counted baseline (counted), several free republications in one month, archive-republish-archive-republish chains, and a limit-0 or exhausted account (free still works, counted refused).
- **Fingerprint migration (AC)**: the existing data migration `0015_identity_fingerprint_v9` already recomputes fingerprints when the identity set changed. Add the reusable pattern and a test that proves the AC: a registry change that adds an identity field recomputes stored fingerprints without consuming quota or writing events (parametrise the existing migration test or add a helper used by it); document in `docs/listings.md` the exact procedure a future registry change must follow (new data migration with frozen canonicalisation, test).
- **Endpoint**: `POST /api/v1/listings/{id}/republish`, body `{base_revision}`, header `Idempotency-Key` (same validation as publish), CSRF, owner only, `Cache-Control: no-store`; `200` with the owner Listing resource plus `publication_kind` (`republish_counted` or `republish_free`) so the client can show the outcome (use a dedicated serializer for this response; leave the publish response unchanged); documented responses 200, 400, 401, 403, 404, 409, 422. Regenerate `openapi.yaml`, `docs/authorisation-matrix.md`; update `docs/listings.md` (republication, counted vs free, baseline rule, restricted/sold/rented rules), `docs/visibility.md` (a republished listing is public again under the 2.12 policy; verify with a test), `docs/account-states.md` if the matrix changes. Update the lifecycle table in the docs: archived has exactly one outgoing path, `publication.republish`; update the single-writer AST guard tests so `republish` in `publication.py` may set published and nothing else may.
- **Interactions to verify**: archive then republish keeps images and revision history; the sample data command is unaffected; draft retention and the open-draft cap ignore archived/republished listings; owner edits while Archived remain allowed and identity edits while Archived produce a counted republish (test the full chain: publish, archive, change `city`, republish => counted; archive, change `description`, republish same month => free); quota allowance endpoint reflects counted republications and ignores free ones; concurrency: 20 simultaneous republish requests against one remaining slot (exactly one counted success), two listings racing for one slot, the same key replayed concurrently charges once; a publish and a republish never both succeed for one Listing.
- **Tests (narrow runs)**: every matrix row below plus the paragraph above; mutation-check the same-month rule, the fingerprint equality, the baseline selection (seed/free events ignored), the restricted refusal, the sold/rented refusal and the counted quota check. Lessons from earlier stories apply (static messages, whole-body checks, low-entropy fake words, events after commit only, fault injection).

**Always (frontend, after the backend is merged and `npm run api:sync`):**
- On the Archived Listing page replace the "Republishing will be available in a later update" notes (Archived note, Archive dialog copy) with a **Republish** section: copy "Republishing makes this listing visible to everyone again. It uses 1 of your monthly publications unless you republish in the same month as your last counted publication without changing the category, subtype, purpose, city, locality or area." and the remaining allowance (existing allowance read); a Republish button (aria-disabled with the reason while there are unsaved changes: "Save your changes before republishing", or while a save/upload/delete/status action runs; locks like Publish), `POST .../republish` with CSRF, `base_revision` and an `Idempotency-Key` per attempt with the same retry/new-key rules as Publish (reuse the Publish machinery: share code, do not copy), validation errors shown exactly like Publish (summary with links, next to fields, images error, confirm-required with the Confirm button), quota_exhausted message with the dashboard link and the Karachi reset date, email-unverified and suspended messages, stale/conflict handling as in Publish, 403 for a restricted listing ("This listing cannot be republished."), success: the page switches to the Published editor with a polite announcement stating the outcome ("Republished. This used 1 of your publications." for `republish_counted`, "Republished. This did not use any of your publications." for `republish_free`) and the allowance re-read. Sold and Rented pages show a short note "Archive this listing first to republish it." beside the existing Archive action (no Republish button there); Removed and restricted keep no Republish button (restricted Archived listings show a note that it cannot be republished). Stub backend: the republish route mirroring every backend rule above including the counted/free decision (the stub keeps per-listing counted-event month and fingerprint of the eight identity fields), `publication_kind` in the response, error modes; spec file with axe at desktop and mobile in the key states (archived with Republish, errors, quota exhausted, success outcomes), README, no new proxy behaviour. Existing specs touching the Archived page copy (listing-lifecycle) must be updated; run the Draft/Listing page specs listed in the 2.17 spec.

**Never:** set `published` anywhere but `listings.publication`; republish a restricted, removed, sold or rented Listing; consume quota for a free republication or on any refusal; treat a seed or free event as the quota baseline; skip full revalidation; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Same month, identity unchanged | archived, last counted event same Karachi month, fingerprint equal | `200`, `republish_free`, counted false, no quota used | N/A |
| Different month | last counted event in an earlier Karachi month | `republish_counted`, needs allowance, uses one | `409 quota_exhausted` if none left |
| Identity changed | any of the eight identity fields differs | `republish_counted` | as above |
| No counted baseline | seeded or free-only history | `republish_counted` | as above |
| Sold or Rented | owner | `409 conflict` (archive first) | envelope |
| Restricted | owner | `403 forbidden`, nothing written | envelope |
| Not archived | draft / published / removed | `409 conflict` | envelope |
| Incomplete archived data | missing required item, no image | `400` listing every problem, nothing consumed | envelope |
| Email-unverified / Suspended | account state | `403 email_not_verified` / `publishing_unavailable` | envelope |
| Same key replay / other content | idempotency | original result, one charge / `422` | envelope |
| 20 concurrent, one slot | threads | exactly one counted success | N/A |
| Stale revision | old base | `409 stale_revision` + current | envelope |
| Other account / admin / Visitor | any | `404` / `404` / `401` | envelope |

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

## Verification

**Backend (narrow while developing, full in CI):** `uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run lint-imports && uv run python manage.py makemigrations --check --dry-run && uv run python manage.py spectacular --validate --file openapi.yaml`, then `scripts/test-changed.sh` and, before the merge, one parallel full run `env -u DJANGO_SETTINGS_MODULE uv run pytest -n 3 -q`.
**Frontend:** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && npm run check:prod-admin && npm run check:prod-listings && npm run check:prod-profile`, the new and changed specs, the Draft/Listing-page specs, and `npm run test:e2e:changed`; the full suite runs in CI.
