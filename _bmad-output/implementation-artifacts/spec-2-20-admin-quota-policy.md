---
title: 'Story 2.20: Administrator quota policy management (backend half, then frontend half)'
type: 'feature'
created: '2026-11-26'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Story 2.20)'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-14-publication-quota-policy-and-allowance.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/administrators.md'
  - '{project-root}/docs/testing.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The monthly limits (2 and 10) can only be changed by a migration.

**Approach:** Admin-only read of the current policy and this month's usage by account, and a change of one account type's limit that takes effect from now (later attempts only), audited. Frontend: an admin quota screen. Final-year project: keep it small and plain.

## Boundaries & Constraints

**Backend:**
- `listings` service `set_quota_limit(actor, account_type, monthly_limit, now=None)`: administrator only (same guard and refusals as the other admin services: non-admin 404/403 as the existing admin routes do, Visitor 401); `account_type` individual|agency; `monthly_limit` integer 0..1000 (else `400 validation_error`); in one transaction lock the current open row for the type, set its `effective_to = now`, then insert the new open row `effective_from = now` (the exclusion constraint is not deferrable: close first); the same limit as current is a no-op (200, no row, no audit); audit `quota.policy_changed` (account type, old, new). Concurrent changes serialise cleanly (one lock per type; test with threads). Past rows are never modified otherwise; allowance and publication already resolve the limit at attempt time (test that a change applies to the next attempt and that history for earlier instants is unchanged).
- Admin routes (follow the existing admin route and permission conventions and the admin session): `GET /api/v1/admin/quota-policy` → `{"items":[{"account_type","monthly_limit","effective_from"}]}` (the current rows); `PUT /api/v1/admin/quota-policy/{account_type}` body `{"monthly_limit": n}` (CSRF) → the new current row; `GET /api/v1/admin/quota-usage` → this Karachi month's accounts that have used at least one counted publication, newest usage first, cursor paginated (follow the existing pagination pattern): `{"account_id","email","account_type","used","limit"}` plus `period_start`/`resets_at`. Emails come through `identity.selectors` (listings must not import identity models). All `no-store`. Register in the admin-route authz tests, regenerate openapi and the authz matrix, update `docs/listings.md`.
- Tests: guard matrix on all three routes, validation, effective-dating, no-op, audit, concurrency, usage counting (counted events only, current month only, other months excluded), pagination.

**Frontend (after merge and `npm run api:sync`):** an admin "Publication limits" page in the existing admin area (nav link, server-guarded like the other admin pages): current limits per account type with an edit form each (number input 0..1000), a confirmation dialog stating old → new and "applies to future publications only", success announcement, errors; a usage table (email, type, used / limit) with load more, loading/empty/error states. Stub routes, a short spec with axe, README. No new proxy behaviour.

**Never:** let a non-administrator read or change policy or usage; rewrite past policy rows beyond closing the open one; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior |
|----------|--------------|---------------------------|
| View | admin | current limits and this month's usage |
| Change limit | admin, new value | old row closed, new row from now, audited; later attempts use it |
| Same value | admin | no change, no audit |
| Invalid value / type | admin | `400` / `404` |
| Non-admin / Visitor | any route | existing admin refusals |
| Two concurrent changes | threads | serialised, one open row |

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

## Verification

Backend: ruff, format, lint-imports, makemigrations --check, spectacular, narrow tests and one `pytest -n 3` full run. Frontend: lint, typecheck, build, api:check, check:prod-admin, new specs, `npm run test:e2e:changed`; full suite in CI.
