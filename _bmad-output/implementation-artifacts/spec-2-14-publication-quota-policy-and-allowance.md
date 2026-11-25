---
title: 'Story 2.14: Publication quota policy and allowance view (backend half, then frontend half)'
type: 'feature'
created: '2026-11-25'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Story 2.14)'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-7, AD-22)'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-13-draft-preview-and-management.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/testing.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Owners publish within a monthly allowance (2 for Individuals, 10 for Agencies), but nothing stores that policy, nothing counts publications, and the owner cannot see what is left.

**Approach:** Backend (`listings` owns quota policy and publication events, AD-7): effective-dated `ListingQuotaPolicy` rows (seeded 2 and 10), the empty `PublicationEvent` table (written later by Story 2.15), a pure allowance computation in Karachi calendar-month terms and an owner-only read endpoint. Frontend: the dashboard shows the allowance. No publishing, no enforcement and no admin editing here (2.15 and 2.20). Backend first and merged, then frontend. Workflow: narrow local test runs (`docs/testing.md`), full suites in CI; decide ambiguities yourself with the best option and report them.

## Boundaries & Constraints

**Always (backend):**
- **Policy (`listings`)**: model `ListingQuotaPolicy` (uuid id, `account_type` in {`individual`,`agency`} checked against the identity enum values without importing identity models, `monthly_limit` integer >= 0 (CHECK), `effective_from` timestamptz not null, `effective_to` timestamptz null (exclusive end), `created_at`; CHECK `effective_to IS NULL OR effective_to > effective_from`). Rows for one `account_type` can never overlap: enforce it in the database with an exclusion constraint on `(account_type WITH =, tstzrange(effective_from, effective_to) WITH &&)` (needs the `btree_gist` extension: add it to the migration with `CreateExtension` and to `docker/postgres/create_roles.sh` / the CI database setup if the migrator role cannot create it; check how `vector` is provisioned and mirror that). If the extension cannot be provisioned reliably in all environments (local, CI service container, prod settings tests), fall back to a partial unique index on `account_type` where `effective_to IS NULL` plus a validated service-level overlap check, and say so in the triage. Seed (data migration, idempotent, written only by the migration and, later, Story 2.20's service): individual 2 and agency 10, `effective_from` = 2000-01-01 00:00 UTC, open-ended. Selector `quota_limit_at(account_type, at)` returns the limit of the row whose range contains `at` (half-open), or raises a clear internal error if none (the seed guarantees one). A policy change takes effect only for attempts at or after its `effective_from`: the limit is always resolved at the time of the attempt, never stored on old events.
- **Events**: model `PublicationEvent` (uuid id, `account_id` uuid and `listing_id` uuid as plain columns with no foreign key so quota history survives listing deletion, `kind` in {`initial`, `republish_counted`, `republish_free`, `restore`, `seed`}, `counted` boolean, nullable `identity_fingerprint` text, `correction_used` boolean default false, nullable `idempotency_key` text with a unique constraint per account where not null, `created_at` timestamptz default now). CHECK `counted` is true only for kinds `initial` and `republish_counted`. Index on `(account_id, created_at)` for the monthly count. The table ships empty; nothing in this story writes to it except tests that insert rows directly. Never updated or deleted by application code (add a note and, if cheap, a test that no service writes it yet).
- **Allowance (pure, in `listings`)**: `allowance_for(account_id, account_type, now)` returns a frozen value `{limit, used, remaining, period_start, resets_at}`: the period is the calendar month of `now` in `Asia/Karachi` (a fixed UTC+5, use `zoneinfo`); `period_start` and `resets_at` are timezone-aware datetimes (resets_at = the first instant of the next Karachi month, so December rolls into January of the next year); `used` is the number of `counted` events of the account with `created_at` in `[period_start, resets_at)`; `remaining = max(limit - used, 0)` (used may exceed limit after a later limit decrease; remaining never negative); `limit` is resolved at `now`. Boundary cases are exact: an event at 2026-11-30 19:00:00 UTC (= 2026-12-01 00:00 Karachi) belongs to December; one second earlier belongs to November; `now` one instant before the boundary is still the old month. Takes `now` as a parameter (the endpoint passes the clock) so tests never depend on wall time.
- **Endpoint**: `GET /api/v1/me/publication-allowance`, session required (Visitor 401), owner accounts only (Individual and Agency, Active and Email-unverified; administrators get the same refusal as the other `/me` owner routes; Suspended and Deactivated follow the existing account-state matrix for read routes), `Cache-Control: no-store`, explicit serializer: `{"limit": int, "used": int, "remaining": int, "period_start": iso8601, "resets_at": iso8601, "timezone": "Asia/Karachi"}` (offset-aware strings, no internal ids, no policy ids, nothing about other accounts). Update `docs/listings.md`, `docs/authorisation-matrix.md`, regenerate `openapi.yaml` (freshness test), and keep the single account-existence-side-channel and after-commit rules of `docs/story-checklist.md` in mind (this route reveals only the caller's own data).
- **Tests** (narrow runs while developing): policy seed idempotency (migration run twice via `MigrationExecutor`), exclusion/overlap rejection at the DB level (or the fallback), CHECKs (negative limit, bad range, bad kind, `counted` with a free kind), limit resolution including exactly at `effective_from` and at `effective_to`, a policy change affecting only later times (old month still resolves to the old limit when asked for an old `now`), allowance: empty table => used 0, remaining = limit; events inside/outside the month, uncounted kinds ignored, other accounts' events ignored, boundary seconds, year rollover, remaining floor at 0, Karachi vs UTC month difference, per account type (2 and 10); endpoint matrix (Individual, Agency, unverified, Suspended, Deactivated, administrator, Visitor, no-store, shape without extra keys, no leakage of another account's counts); the service/selector never writes. Lessons from earlier stories apply (whole-body checks, static messages, low-entropy fake words, no vacuous assertions, mutation-check the month boundary and the counted filter).

**Always (frontend, after the backend is merged and `npm run api:sync`):**
- The dashboard (`src/app/(app)/dashboard/page.tsx` and a client component) shows, for Individual and Agency accounts, a "Publications this month" card: "N of M used, R remaining", a progress element with an accessible name and values (a real `<progress>` or `role="progressbar"` with `aria-valuenow/min/max`), and "Resets on <date>" formatted in Asia/Karachi with the month name (never relying on the browser's time zone; test with the browser time zone set to something else). States: loading, failed with Retry, remaining 0 ("You have used all your publications this month. The allowance resets on ..."), singular/plural ("1 publication remaining"), unverified account still shows it. Fail-closed parsing (all numbers non-negative integers, `remaining <= limit`, dates parse, `timezone` equals `Asia/Karachi`, `used` may exceed `limit` only with `remaining` 0). Administrators see no card and no request. Stub backend route and modes (ok, empty, exhausted, error, broken, hang, 401), tests including axe at desktop and mobile, README, and a production-build check only if a proxy or caching behaviour is relied on (the route is `no-store`).

**Never:** enforce or consume quota here; write `PublicationEvent` from any service; store a limit on an event; compute months in UTC or the server's local zone; expose policy rows or other accounts' data; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh Individual / Agency | no events | limit 2 / 10, used 0, remaining = limit, resets at the next Karachi month start | N/A |
| Counted events this month | initial / republish_counted rows | `used` counts them | N/A |
| Free or other-month or other-account events | republish_free, restore, seed, last month, another account | not counted | N/A |
| Karachi month boundary | event 1 s before / at 00:00 Karachi on the 1st | previous month / new month | N/A |
| December | now in December | `resets_at` is 1 January next year Karachi | N/A |
| Limit lowered below usage | used 3, limit 2 | remaining 0, used still 3 | N/A |
| Policy change | new row effective later | old times still see the old limit, later times the new | N/A |
| Overlapping policy row | same type, overlapping range | rejected by the database | IntegrityError |
| Visitor / administrator | no session / admin session | 401 / the existing owner-route refusal | envelope |
| Frontend exhausted | remaining 0 | message with the reset date, progress full | N/A |

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

## Verification

**Backend (narrow while developing, full in CI):** `uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run lint-imports && uv run python manage.py makemigrations --check --dry-run && uv run python manage.py spectacular --validate --file openapi.yaml`, then `scripts/test-changed.sh` (changed dirs plus guards) and, before the merge, one parallel full run `env -u DJANGO_SETTINGS_MODULE uv run pytest -n 3 -q`.
**Frontend:** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && npm run check:prod-admin && npm run check:prod-listings && npm run check:prod-profile`, the new and changed specs and `npm run test:e2e:changed`; the full suite runs in CI.
