---
title: 'Story 2.21: Administrator account suspension and reactivation (backend half, then frontend half)'
type: 'feature'
created: '2026-11-26'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Story 2.21)'
  - '{project-root}/_bmad-output/planning-artifacts/prds/prd-estate-ai-backend-2026-11-18/prd.md (FR-59, FR-74)'
  - '{project-root}/docs/account-states.md'
  - '{project-root}/docs/administrators.md'
  - '{project-root}/docs/audit.md'
  - '{project-root}/docs/visibility.md'
  - '{project-root}/docs/notifications.md'
  - '{project-root}/docs/testing.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** There is no way to stop an abusive account short of editing the database.

**Approach:** Admin-only: look up an Individual or Agency account by email, suspend it with a reason, reactivate it. Suspension is derived everywhere from the account state (no Listing status changes). Final-year project: keep it plain, no workflow, no appeals.

## Boundaries & Constraints

**Backend:**
- Admin lookup follows the existing reasoned private-read convention (`docs/audit.md`: POST, short reason, `privacy.viewed` audit): returns id, email, account type, display name, state, email verified, suspension reason and date. Only Individual and Agency accounts (an administrator or unknown address: the same `not_found`).
- `identity` services `suspend_account(actor, account_id, reason)` and `reactivate_account(actor, account_id, reason)`: administrator only; reason required, trimmed, 5..500 chars; suspend: Active or Email-unverified → Suspended (store reason and time); already Suspended or Deactivated → `409 conflict`; reactivate: Suspended → Active if the email is verified else Email-unverified; otherwise `409`. One transaction with the account lock; audit `account.suspended` / `account.reactivated` (ids only, reason kept on the account row, not in logs); a notification to the account (new kinds, no reason text in the payload) after commit; no Listing rows change.
- Effects (reuse the existing state matrix, verify each by test): suspended account cannot publish, republish, correct, edit drafts or listings, mark sold/rented; may view and archive; its Listings leave public visibility (public projection and public image route) and return on reactivation. Sessions: keep existing sessions if the matrix already restricts by state; decide and document (recommended: keep sessions; state is read on every request).
- The account sees the reason at sign-in: expose `suspension_reason` to the account itself only (session/me response, only when Suspended); never public, never to other users. "Cannot send Messages" is a later epic: note it in docs.
- Routes under the admin API (admin session, CSRF, no-store), registered in the admin authz tests; regenerate openapi and the authz matrix; update docs/account-states.md, administrators.md.
- Tests: guard matrix, validation, every transition and refusal, effects above, reactivation for verified/unverified, notification and audit, reason never in public shapes or logs, concurrency (two suspends: one wins).

**Frontend (after merge and api:sync):** admin "Accounts" page: email lookup form with reason-for-lookup, result card with state, Suspend / Reactivate with a reason field and confirmation dialog, success and error states; the signed-in suspended user sees a banner with the reason (header or dashboard). Stub, short spec with axe, README.

**Never:** change Listing statuses on suspension; let a non-administrator call these routes; expose the reason to anyone but the account and administrators; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior |
|----------|--------------|---------------------------|
| Look up | admin, existing email + lookup reason | account summary, audit privacy.viewed |
| Suspend Active/unverified | admin, reason | Suspended, audit, notification, listings hidden |
| Suspend again / reactivate non-suspended | admin | `409` |
| Reactivate | verified / unverified | Active / Email-unverified, listings visible again if Active |
| Admin or unknown email lookup | admin | `404` |
| Non-admin / Visitor | any | existing admin refusals |

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

Backend (one reviewer): no medium-or-higher findings. Accepted (low, pre-existing): a field edit already in flight can commit just after a suspension (`apply_updates` locks only the listing). Reason limits: change reason 5..500, lookup reason 10..300.

## Verification

Backend: ruff, format, lint-imports, makemigrations --check, spectacular, narrow tests and one `pytest -n 3` full run. Frontend: lint, typecheck, build, api:check, check:prod-admin, new specs, `npm run test:e2e:changed`; full suite in CI.
