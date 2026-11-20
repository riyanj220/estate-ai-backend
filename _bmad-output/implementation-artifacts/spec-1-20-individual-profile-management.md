---
title: 'Story 1.20: Individual profile management (backend half)'
type: 'feature'
created: '2026-11-20'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-15-individual-registration.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-16-email-verification.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-18-password-reset-and-change.md'
  - '{project-root}/docs/registration.md'
  - '{project-root}/docs/password-reset.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An Individual cannot see or correct the details they registered with, and nobody can change the email address on an account.

**Approach:** Add a signed-in profile read and update (name and phone, phone private) and a verified email change: a request that needs the current password and sends a single-use link to the NEW address, a confirm that swaps the address only after the link is used, with the old address valid until then and no disclosure of whether the new address is already registered. This spec is the backend half; the frontend page is a second spec after the backend merges. It reuses the validators of Story 1.15, the token and event patterns of Stories 1.16 and 1.18, and the CSRF and session design of Story 1.17.

## Boundaries & Constraints

**Always:**
- **Profile read** `GET /api/v1/me/profile`, `RequireSignedIn`, Individual accounts only (any other account type is `403 forbidden`), `Cache-Control: no-store`. Returns `{"full_name","phone","email","pending_email"}`: `phone` is the owner's own E.164 number (private class, only ever returned here, to the owner), `email` the current address, `pending_email` the address of the newest unused unexpired email-change request or null. Nothing else.
- **Profile update** `PATCH /api/v1/me/profile`, same guards plus CSRF (existing authentication class), strict body: only `full_name` and `phone` allowed, at least one present, unknown fields refused (`400 validation_error`, `unknown_field`), `email` is NOT updatable here. Validation uses the Story 1.15 `check_full_name` and `check_phone` (so the phone stays mandatory: a blank or null `phone` is `required`, never a way to remove it), all failing fields reported at once with stable codes, values never echoed. On success `200` with the same body as the read, `no-store`; one audit record `account.profile_updated` (empty metadata, registered from `AppConfig.ready`) only when something actually changed; an unchanged submission changes nothing and writes no audit record. Updates are serialised with the account row lock.
- **Email change request** `POST /api/v1/me/email-change`, `RequireSignedIn`, CSRF enforced, not available to Administrators (`403 forbidden`), body strict `{"new_email","current_password"}`. The current password is verified with `check_password` under the SAME failed-attempt throttle and serialisation as the password change (`password_change_failed`, 5 per 900 s per account, advisory lock, silent `incorrect` when capped, never 429 from it): a wrong or capped current password is `400 validation_error` with `fields.current_password = "incorrect"`. `new_email` uses `check_email` (`required`, `invalid`, `invalid_email`). Requests are limited by a new seeded policy `email_change_request` (5 per hour, key kind `account`, idempotent kernel migration) answering the standard 429 with `Retry-After`; this 429 is the owner's own counter and reveals nothing about any address. On acceptance ALWAYS `202 {"status":"check_your_email"}` with identical status, body and headers whether the new address is free, already registered to another account, equal to the current address (any case), or unusable: the response path does the same kind of work in every branch and the effects happen after commit through an event `account.email_change_requested` carrying only the account id and the request id of a new row. The handler (identity subscribers, after commit, own transaction, failures logged by exception class only) behaves as follows: if the new address is free, queue the confirmation email (fixed copy, link `email_link("/confirm-email-change", {"token": ...})`, "works once and expires in 24 hours") to the NEW address and a short notice to the CURRENT address ("a change of your sign-in email was requested; if it was not you, change your password") ; if the new address is registered to another account, send that address the existing "already registered" notice instead (never a link) and nothing to the current address; if it equals the current address, do nothing. Per requesting account the existing silent `verification_email` cap (5 per hour) applies to the confirmation sends. The current address stays the sign-in address and remains valid until confirmation; no sign-in change happens before.
- **Token** `EmailChangeToken` in `identity` (UUID pk, FK `Account` CASCADE, `new_email` CharField 254 (the address as typed, trimmed), `token_hash` SHA-256 unique, `created_at`, `expires_at` = creation + 24 hours, `used_at` nullable), one migration with indexes for retention; plain token via the existing `verification` helpers, shown only in the email link, never stored, logged, audited, evented or echoed; issuing a new one supersedes every earlier unused change token of the account. Retention job `identity.email_change_tokens` (used or expired more than 7 days ago) registered through `kernel.retention.register`.
- **Email change confirm** `POST /api/v1/email-changes/confirm` body strict `{"token"}`, public (`AllowPublic`, `authentication_classes = []`, so no CSRF and cookies are ignored, like verification confirm), per-client throttle reusing the seeded `password_reset_confirm` policy scope or a new seeded `email_change_confirm` (20 per hour, key client) answering the standard 429, `Cache-Control: no-store`. Under locks (account then token, same order as 1.16) it checks well-formed, known, unused (`link_used`), unexpired (`link_expired`), account not Deactivated (`link_invalid`), then that the new address is STILL free case-insensitively (otherwise `link_invalid`, no disclosure of why), then in one transaction sets the new address on the account, marks the token used and supersedes other unused change tokens, verifies the email if it was not verified (the link proves ownership; `mark verified` path and its audit as in Story 1.16, state rules unchanged), writes the audit record `account.email_changed` (empty metadata) and queues, after commit, a notice to the OLD address ("your sign-in email was changed to a new address; if this was not you, contact support"; no new address in the text). Returns `200 {"status":"email_changed"}`. Sessions are NOT ended (the change required the password); the unique case-insensitive email constraint is the last line of defence against races (an `IntegrityError` becomes `link_invalid`).
- Reuse the error codes `link_invalid`, `link_expired`, `link_used`; add static field messages only if needed. Serializers use the shared `bodies` helpers; no private names imported across modules.
- **Privacy**: the phone, the current and new email addresses and tokens never appear in logs, audit metadata, errors, events (events carry ids only) or emails other than the intended recipients' fixed copy; the new address is the recipient of its confirmation email and appears nowhere else. Canary tests over logs, audit, events, errors and the mail outbox. `/api/v1/me` (the actor summary) is unchanged and never carries the phone.
- Docs: new `docs/profile.md` (routes, fields, privacy classes, the email-change flow, enumeration rule, rate limits, what the frontend must do), updates to `docs/rate-limits.md`, `docs/sessions.md` (email changes do not end sessions) and `README.md`; `openapi.yaml` and `docs/authorisation-matrix.md` regenerated. All earlier tests, convention tests and import-linter contracts keep passing. Comments only where intent is not obvious.

**Never:** return the phone to anyone but the owner or in any route other than the profile read/update responses; allow the profile route to change the email, the account type or any other field; replace the sign-in address before the new address confirms; reveal whether a new address is registered (status, body, headers, 429, timing path); send a confirmation link to an address that belongs to another account; log or audit a phone, an email or a token; allow a wrong current password to be probed without the shared throttle; return 429 from the failed-password throttle; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Read | signed-in Individual | 200 `{full_name, phone, email, pending_email}`, `no-store` | N/A |
| Read, wrong type | Agency or Admin session | 403 `forbidden` | envelope |
| Read, no session | | 401 `unauthenticated` | envelope |
| Update name | valid `full_name` | 200 new body; audit `account.profile_updated`; other fields untouched | N/A |
| Update phone | valid Pakistani or international number | 200, stored E.164 | N/A |
| Update both / unchanged | same values | unchanged submission: 200, no audit record | N/A |
| Phone removal | blank or null `phone` | 400 `phone: required`; nothing changes | envelope |
| Invalid fields | bad name and bad phone together | both reported at once with stable codes | envelope |
| Unknown or email field | `email`, `account_type`, anything else | 400 `unknown_field`; nothing changes | envelope |
| Empty body | `{}` | 400 validation error | envelope |
| No CSRF | PATCH or POST without a token | 403 `forbidden` | envelope |
| Change request, free address | right password, valid new address | 202; after commit confirmation email to the new address and notice to the old; sign-in address unchanged | N/A |
| Change request, taken address | address of another account (any case) | identical 202; the other address gets the "already registered" notice, no link; nothing to the old address | N/A |
| Change request, same address | current address in another case | identical 202; nothing sent | N/A |
| Enumeration | free vs taken vs same vs capped | same status, body and headers apart from request id | test |
| Wrong current password | | 400 `current_password: incorrect`; throttle counted | envelope |
| Throttle | 5 wrong attempts then the right one (shared with password change) | `incorrect` during the window; no 429 from it; clears after window | test with clock |
| Request limit | 6th request in an hour for one account | 429 with `Retry-After` | envelope |
| Invalid new address | malformed | 400 `new_email: invalid_email` | envelope |
| Admin | admin session | 403 `forbidden` | envelope |
| Pending | after a request | profile read shows `pending_email`; older tokens superseded | N/A |
| Confirm ok | newest unused unexpired link, address still free | 200 `email_changed`; account email replaced; sign-in works with the new address and not the old; token used; email verified; audit `account.email_changed`; notice to the old address after commit; sessions untouched | N/A |
| Confirm expired / used / unknown | | 410 `link_expired` / 409 `link_used` / 400 `link_invalid`, token not echoed | envelope |
| Confirm, address taken meanwhile | another account registered the address after the request | 400 `link_invalid`; nothing changes | envelope |
| Confirm, deactivated account | | 400 `link_invalid` | envelope |
| Concurrent confirm | many parallel confirms | exactly one 200, others `link_used` (threaded, PostgreSQL) | N/A |
| Two accounts, one address | race on the unique index | one wins, the other `link_invalid`, no 500 | test |
| Confirm throttle | over the per-client limit | 429 with `Retry-After` | envelope |
| Old address until confirm | before the link is used | the old address still signs in; the new one does not | test |
| Privacy | logs, audit metadata, events, errors, mail bodies | no phone, no address or token outside the intended recipients' fixed copy (canary) | test |
| Storage | token row | hash only | test |
| Retention | `run_retention` | old tokens purged; dry run counts | N/A |
| Failure | mail backend raises in the handler | logged by class; response unaffected | N/A |
| Contract | `openapi.yaml` | routes, bodies, field messages, freshness passes | test |

</frozen-after-approval>

## Code Map

- `apps/identity/models.py`, `migrations/0007_email_change_token.py` -- `EmailChangeToken`
- `apps/identity/validation.py` (reuse), `services.py` (profile read/update, request, confirm), `selectors.py` (profile view), `notices.py`, `events.py`, `subscribers.py`, `apps.py` (audit actions, retention)
- `apps/kernel/migrations/0008_seed_email_change_policies.py` -- `email_change_request` (and `email_change_confirm` if used)
- `apps/presentation/api/profile.py` (views, serializers), `config/urls.py`
- `openapi.yaml`, `docs/authorisation-matrix.md` regenerated
- `tests/identity/test_profile.py`, `tests/api/test_profile_api.py`, seed and retention tests -- every matrix row
- `docs/profile.md`, `docs/rate-limits.md`, `docs/sessions.md`, `README.md`

## Tasks & Acceptance

**Execution:**
- [ ] profile read and update services, audit, endpoints
- [ ] email change token, request service with shared throttle, event handler and notices
- [ ] confirm service and endpoint, retention, seeds
- [ ] tests covering every matrix row; docs and regenerated contracts

**Acceptance Criteria:**
- Given I am signed in, when I update my name or phone, then the change is saved, the phone remains mandatory and private, and only I can read or change it.
- Given I request an email change, when I submit a new address, then it must be verified before it replaces the old one, which stays valid until then, and uniqueness rules of registration apply without disclosing existing addresses.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, backend half (blind, edge-case, verification-gap).

| # | Finding | Verdict | Route |
|---|---------|---------|-------|
| 1 | A password change or reset left an attacker's already-mailed email-change link valid for 24 hours (blind) | high | patch: change, reset and deactivation void every unused change link in the same transaction |
| 2 | The heads-up mail went only to the current address on the free branch, so the owner could tell whether a new address is registered (blind) | high | patch: sent on every accepted request |
| 3 | The request limit counted wrong passwords, so typos or a stolen session burned the budget and made the silent `incorrect` unreachable (blind, edge, verification-gap) | medium | patch: counted only after the password verifies |
| 4 | Every accepted request superseded the pending link, including no-ops, and `pending_email` showed addresses that never got a link (blind, edge, verification-gap) | medium | patch: `link_issued` flag, supersede only when a real link is minted, pending shows issued rows only |
| 5 | Mail bombing of a target address through many accounts (blind, edge) | low | patch: per-target silent cap |
| 6 | Same-address and taken checks mixed Python and database case folding (blind, edge) | low | patch: database comparison |
| 7 | PATCH returned 400 before 403 for other account types; the profile view repr exposed phone and emails (edge, blind) | low | patch |
| 8 | Tests: handler rollback, exact error codes, capped branch, query-shape equality, expiry, supersession, notice on rollback, canary casing, per-account 429 (verification-gap) | medium | patch: 22 tests |
| 9 | Confirm leaves other sessions open and the old-address notice says only "contact support" (blind, edge) | medium | defer, accepted and documented: the password was required to start the change and the notice is the revert signal |
| 10 | Suspended accounts can change their email; mail timing after commit still differs by branch (edge, blind) | low | defer, documented; same residual as Stories 1.16 and 1.18 |

Deviation recorded: the heads-up is sent whenever the account is eligible, even if the row is already used or issued; handlers that run out of order leave the last one owning the link.

## Design Notes

Decisions made without asking: the email change needs the current password (a stolen session alone cannot redirect an account) and shares the password-change failure throttle so the two routes cannot be used to probe the password separately; the confirmation link goes only to the new address and the old address is notified, so a hijack is visible to the real owner; taken addresses get the existing "already registered" notice rather than a link; the confirm is public like verification so the link works in any browser, and sessions are not ended because the password was already required; the profile read is Individual only because Agency profile management is Story 1.21, but the email change is available to Individual and Agency accounts; the phone only appears in the owner's profile responses.

## Verification

**Commands (backend):**
- `uv run python manage.py migrate --database=migrator`
- `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run pytest -q && uv run python manage.py makemigrations --check --dry-run`
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- regenerate and commit
