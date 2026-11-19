---
title: 'Story 1.16: Email verification (backend half)'
type: 'feature'
created: '2026-11-19'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'c44290ba444223c191cfc8477931d753820305ea'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-15-individual-registration.md'
  - '{project-root}/docs/registration.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Registered accounts stay Email-unverified forever: no verification message is sent and no link can be confirmed, so no account can ever become Active.

**Approach:** Send a verification email with a single-use, 24-hour link whenever an account registers (or asks again), confirm it through a public endpoint that moves the account to Active, and let a Visitor request a new link without revealing whether the address is registered. This spec is the backend half; the frontend result page is a second spec written after the backend merges.

## Boundaries & Constraints

**Always:** a verification token is `secrets.token_urlsafe(32)` shown only in the email link, stored only as its SHA-256 hash in a new `EmailVerificationToken` model (UUID primary key, FK to `Account`, `token_hash` unique, `created_at`, `expires_at` = creation plus 24 hours, `used_at` nullable); issuing a new token marks every earlier unused token for that account as used so only the newest link works; handlers for the identity events `account.registered` and `account.verification_requested` are registered in `identity`'s `subscribers.py` through `AppConfig.ready`, run after commit, and in a fresh transaction issue the token and queue the email through the kernel mail port (plain text, fixed copy: subject "Verify your EstateAI email address", body with the link and "This link works once and expires in 24 hours."), the link built with `email_link("/verify-email", {"token": token})` from `FRONTEND_BASE_URL`; a subscriber failure is logged by exception class only (existing event rules) and never changes any response; confirmation is `POST /api/v1/email-verifications/confirm` with body `{"token": "<token>"}` (POST so link prefetchers cannot consume it), public (`AllowPublic`), strict single-field serializer, and on success returns `200 {"status":"verified"}` after marking the token used and calling the existing `mark_email_verified` path in one transaction (the account becomes Active unless suspended or deactivated, whose derived state still wins); failures return the standard envelope with three new catalogue codes published in the schema: `link_invalid` (400, unknown, malformed or oversized token), `link_expired` (410, past `expires_at`), `link_used` (409, already used or superseded); the confirm lookup locks the token row (`select_for_update`) so concurrent confirmations produce exactly one success; confirming for an already verified account with a fresh unused token succeeds idempotently (token marked used, no second audit record); requesting a new link is `POST /api/v1/email-verification-requests` with body `{"email": "<address>"}`, public, which always returns `202 {"status":"check_your_email"}` with identical status, body and headers whether the address is unknown, unverified, verified, over a cap, suspended or deactivated; it is limited per client by a new seeded policy `verification_resend` (5 per hour, key kind `client`, added by a new idempotent kernel seed migration) which answers the standard 429 with `Retry-After`; per account it reuses the existing `verification_email` cap (5 per hour), and over that cap it silently sends nothing while the response stays identical; for an existing unverified account it emits `account.verification_requested`, for an existing verified account it sends the existing "already registered" sign-in notice (shared cap), and for unknown, suspended or deactivated accounts it does nothing; the password-hash timing guard of Story 1.15 is not needed here but no branch may do materially different observable work in the response path (all work after commit via events); the token, its hash, the email address and the link never appear in any log record, audit metadata, error body or event payload (events carry only the account id); the audit action `account.email_verified` from Story 1.12 is the only audit write; expired and used tokens older than 7 days are purged by a retention job `identity.email_verification_tokens` registered in `identity`'s `AppConfig.ready` through `kernel.retention.register` with `purge(now, dry_run)` semantics; the runtime role gets DML through the existing hook; `openapi.yaml` and `docs/authorisation-matrix.md` are regenerated; the registration documentation and the Story 1.15 hand-off notes are updated; all earlier tests, convention tests (including the subscribe-location rule and the single contact-regex rule) and import-linter contracts keep passing; comments only where intent is not obvious.

**Never:** build sign-in, sessions or any page; verify by GET; store the plain token; reveal whether an address is registered through status, body, headers or the 429 path (the per-client limit applies to every request alike); send mail from inside the registration transaction (always after commit through events); change a Suspended or Deactivated account's state through verification; add an audit record other than `account.email_verified`; add a contact-data regex outside `patterns.py`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Registration | `account.registered` after commit | one token issued, one email with the link queued, link uses `FRONTEND_BASE_URL` | N/A |
| Valid link | confirm with the newest unused unexpired token | 200 `verified`; account Active; token used; one `account.email_verified` audit record | N/A |
| Expired | confirm after `expires_at` | 410 `link_expired`; nothing changes | envelope |
| Used | confirm the same token twice | second call 409 `link_used`; account unchanged | envelope |
| Superseded | confirm an older token after a newer one was issued | 409 `link_used` | envelope |
| Unknown or malformed | random, empty, over 200 characters, wrong type | 400 `link_invalid` (400 validation for a wrong body shape) | no echo of the token |
| Concurrent | many parallel confirmations of one token | exactly one 200, the rest `link_used` (threaded test on PostgreSQL) | N/A |
| Already verified | confirm a fresh token for a verified account | 200 `verified`, no second audit record | N/A |
| Suspended or deactivated | confirm a valid token | verification recorded; derived state stays Suspended or Deactivated; 200 | N/A |
| Resend, unverified | request for an existing unverified address | 202; after commit a new token supersedes the old and a new email is queued | N/A |
| Resend, verified | request for a verified address | identical 202; the sign-in notice is queued | N/A |
| Resend, unknown | unknown address | identical 202; nothing queued | N/A |
| Enumeration | compare responses across unknown, unverified, verified, capped, suspended | same status, body and headers apart from the request id | test |
| Client limit | 6th resend request in an hour from one client | 429 envelope with `Retry-After`, for any address alike | N/A |
| Account cap | 6th resend for one account within an hour from different clients | identical 202; no sixth email | N/A |
| Storage | database row for a token | holds the hash only; plain token never stored | test |
| Privacy | logs, audit, errors, events | no token, hash, email or link | canary test |
| Retention | `run_retention` | tokens used or expired more than 7 days ago are purged; dry run counts only | N/A |
| Contract | `openapi.yaml` | both routes, the new error codes in the enum, freshness passes | test |
| Failure | mail backend raises in the subscriber | logged by class; responses unaffected; token exists; user can request a new link | N/A |
| Rollback | failure after token issue | no token and no email | N/A |

</frozen-after-approval>

## Code Map

- `apps/identity/models.py`, `migrations/0003_email_verification_token.py` -- `EmailVerificationToken`
- `apps/identity/verification.py` -- issue, confirm, purge (pure service functions taking `actor` first where they are public services, in `services.py` if the actor-first convention test requires it)
- `apps/identity/subscribers.py`, `apps/identity/apps.py` -- event handlers and registration; `apps/identity/notices.py` -- verification and existing-address email copy
- `apps/identity/services.py` -- request-new-link service (visitor actor first)
- `apps/kernel/migrations/0006_seed_verification_resend_policy.py` -- idempotent seed; `apps/kernel/errors.py` -- codes `link_invalid`, `link_expired`, `link_used`
- `apps/presentation/api/verifications.py` -- two views, serializers, throttle scope on the request view; `config/urls.py` -- routes
- `openapi.yaml`, `docs/authorisation-matrix.md` -- regenerated
- `tests/identity/test_verification.py`, `tests/api/test_verification_api.py`, kernel seed and role tests -- every matrix row
- `docs/email-verification.md`, `docs/registration.md`, `docs/rate-limits.md`, `README.md` -- behaviour, the enumeration rule, hand-off to the frontend half and to Story 1.17

## Tasks & Acceptance

**Execution:**
- [x] token model, issue and confirm, purge job
- [x] event subscribers and email copy
- [x] resend service, policy seed, error codes
- [x] endpoints, throttling, regenerated contracts
- [x] tests covering every matrix row; docs

**Acceptance Criteria:**
- Given a new account, when registration completes, then a verification email is sent through the mail port with a link built from the configured frontend base URL, and the link is single-use and expires after 24 hours.
- Given a valid link, when I confirm it, then the account becomes email-verified and moves from Email-unverified to Active, and an expired or already-used link returns a clear error so a new link can be requested.
- Given requests for a new link, when I exceed 5 per hour, then the standard 429 envelope is returned, with no disclosure of whether the address exists.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, backend half (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | Token-echo assertions vacuous: mixed-case tokens compared against lowercased text, escape clauses (BH, EC, VG) | high | patch | A logged or echoed token would have passed; assertions now exact-case on every path. |
| 2 | Token or account deleted between lookup and lock gives a 500; NUL or whitespace in the resend email (EC, BH) | medium | patch | Re-fetch under lock, input normalised like registration. |
| 3 | Suspended unverified accounts get a live link on one path but not another (EC) | medium | patch | Issue only for exactly Email-unverified. |
| 4 | Private `events._registry` read for deduplication (BH, EC) | medium | patch | Public `events.is_subscribed`. |
| 5 | No index for the retention purge; seed migration reverse is a no-op (BH, EC) | low | patch | Indexes and a real reverse. |
| 6 | Missing tests: issue guards, expiry boundary, purged token, repeat registration end to end (VG, BH) | medium | patch | Added. |
| 7 | Query-string token leakage mitigations unwritten (BH) | medium | patch | `Cache-Control: no-store` and frontend hand-off requirements documented. |
| 8 | Confirm has no throttle (BH, EC, VG) | low | defer | 256-bit tokens; revisit if abuse appears. |
| 9 | Attacker can use up a victim's per-account resend cap (BH) | low | reject | Accepted for the prototype and documented. |
| 10 | Confirm records verification for suspended or deactivated accounts (BH, EC) | low | reject | Specified in the matrix; documented rationale. |
| 11 | Subscriber failure has no retry or alert; timing differences between known and unknown addresses; OpenAPI length hints; `link_used` covers superseded (BH, EC) | low | reject | Accepted and documented; fix adds complexity. |

## Design Notes

Decisions made without asking: confirmation is a POST so mail scanners that fetch links cannot consume them, the frontend page confirms after it loads; the per-client 5 per hour limit produces the AC's 429 without revealing existence, while the per-account cap is silent for the same reason; three distinct link error codes give the frontend a precise message; tokens are stored hashed with the newest link superseding older ones; all email sending stays after commit through the event bus (Story 1.8) and the mail port (Story 1.13), so a mail failure never affects a response; verification of a Suspended or Deactivated account is recorded but cannot change the derived state (FR-74); the 7-day purge reuses the retention hooks from Story 1.9.

## Verification

**Commands:**
- `uv run python manage.py migrate --database=migrator` -- applies the identity and kernel migrations
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest && uv run python manage.py makemigrations --check --dry-run` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- expected: files regenerated and committed
