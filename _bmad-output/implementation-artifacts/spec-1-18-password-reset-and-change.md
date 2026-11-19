---
title: 'Story 1.18: Password reset and password change (backend half)'
type: 'feature'
created: '2026-11-19'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-16-email-verification.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-17-sign-in-and-sign-out.md'
  - '{project-root}/docs/sessions.md'
  - '{project-root}/docs/email-verification.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An account holder who forgets their password is locked out for good, and a signed-in holder cannot change a password they fear is exposed.

**Approach:** Add a reset by emailed single-use one-hour link (request, then confirm with a new password) and a signed-in password change that needs the current password. Both end the right sessions in the same transaction and write an audit record. This spec is the backend half; the frontend half is a second spec after the backend merges. It mirrors the verification-link design of Story 1.16 and the session design of Story 1.17.

## Boundaries & Constraints

**Always:**
- **Reset token**: model `PasswordResetToken` in `identity` (UUID pk, FK `Account` CASCADE, `token_hash` SHA-256 unique, `created_at`, `expires_at` = creation + 1 hour, `used_at` nullable), one migration with indexes for the retention filters. Token generation, hashing and shape check reuse `identity.verification` helpers (`new_token`, `hash_token`, `well_formed`); the plain token exists only in the email link (`email_link("/reset-password", {"token": token})`) and is never stored, logged, audited, evented or echoed. Issuing a new token marks every earlier unused reset token of the account used, so only the newest link works.
- **Request** `POST /api/v1/password-reset-requests` body `{"email"}` (strict single-field serializer like 1.16), public, `authentication_classes = []`. It ALWAYS answers `202 {"status":"check_your_email"}` with identical status, body and headers whether the address is unknown, unverified, verified, suspended, deactivated or over a cap. Limit per client by a new seeded policy `password_reset_request` (5 per hour, key kind `client`, idempotent kernel seed migration) which answers the standard 429 with `Retry-After` for every request alike. Interpretation of the story's "more than 5 per hour per account returns 429": a 429 tied to one account would reveal that the account exists, so the 429 is per client and the per-account cap (existing seeded `password_reset_email`, 5 per hour, key account) is silent, with the response staying identical; this is recorded in the docs. For an existing account that is not Deactivated (Email-unverified, Active and Suspended may all reset) it emits `account.password_reset_requested` (events carry only the account id); the handler, registered in `identity` subscribers through `AppConfig.ready` and run after commit in a transaction of its own, issues the token and queues the email through the mail port (fixed plain-text copy, subject "Reset your EstateAI password", body with the link and "This link works once and expires in 1 hour. If you did not ask for this, you can ignore this message."). Unknown and Deactivated accounts do nothing. No branch does materially different observable work in the response path (all work after commit). A handler failure is logged by exception class only and never changes a response.
- **Confirm** `POST /api/v1/password-resets/confirm` body `{"token","password"}` (strict two-field serializer), public, `authentication_classes = []`, `Cache-Control: no-store`. Order: well-formed token check, token lookup, then under locks (account first, then token, like 1.16) expiry and used checks, THEN password validation with the registration policy (`check_password(password, account email)`), so field errors appear only for a valid link and a validation failure does not consume the token. Errors: `link_invalid` (400), `link_expired` (410), `link_used` (409) reuse the existing codes; a bad password is `400 validation_error` with `fields.password` set to the policy code (`required`, `password_too_short`, `password_too_long`, `password_matches_email`, `password_too_common`, `invalid`). Deactivated accounts get `link_invalid`. On success, in ONE transaction: store `make_password(new)`, mark the token used, supersede other unused reset tokens, end EVERY open session of the account (reason `password_changed`, directly in the transaction through the identity service, not only through the after-commit event), write the audit record `account.password_reset` (no metadata), emit `account.session_ended` (reason `password_changed`), and queue the "Your password was changed" notice to the account email after commit (fixed copy, no password, no link to act except "If this was not you, reset your password again and contact support"). Returns `200 {"status":"password_reset"}`. A reset does NOT verify the email and does NOT change the account state. The password-hash cost is paid on success only; failures before it do no hashing.
- **Change** `POST /api/v1/me/password` body `{"current_password","new_password"}` (strict two-field serializer), `RequireSignedIn`, CSRF enforced by the existing authentication class, `Cache-Control: no-store`. The current password is verified with Django `check_password`; a wrong current password is `400 validation_error` with `fields.current_password = "incorrect"` (new static field message). Failed checks are throttled by a new seeded policy `password_change_failed` (5 per 900 s, key kind `account`); when the cap is reached the same `incorrect` error is returned without checking (never 429, nothing new revealed), counting failures only, serialised like the 1.17 sign-in (advisory lock, count committed). The new password uses the registration policy with the account email and must differ from the current one (`fields.new_password = "password_same_as_current"`, new static message). On success, in one transaction: store the new hash, end every OTHER open session of the account (the current session stays signed in), write the audit record `account.password_changed` (no metadata), emit `account.session_ended` (reason `password_changed`) carrying `except_session_id` (new optional event field) so the existing handler ends all sessions except that one, queue the "Your password was changed" notice after commit, return `200 {"status":"password_changed"}`. A Deactivated account cannot reach it (it has no session). The current session id reaches the service through the identity the authentication class sets (add what is needed to the Identity value without breaking the actor resolver).
- The event dataclass `AccountSessionEnded` gains `except_session_id: UUID | None = None`; the `end_sessions` handler ends every open session except that id; `identity.services.end_account_sessions` gains the same optional exclusion; reset passes none, change passes the current session.
- Audit: register `account.password_reset` and `account.password_changed` (empty metadata schemas) from `AppConfig.ready`; they are the only audit writes; no email, token, hash or password in metadata.
- Retention job `identity.password_reset_tokens` registered through `kernel.retention.register`: tokens used or expired more than 7 days ago are purged.
- New error codes only if needed; field messages `incorrect` and `password_same_as_current` are added to the static catalogue. `openapi.yaml` and `docs/authorisation-matrix.md` regenerated; new `docs/password-reset.md` (flow, the 429 interpretation, session ending, what the frontend must do) plus updates to `docs/sessions.md` (the accepted-risk note about the password-change emitter is resolved), `docs/rate-limits.md`, `docs/email-verification.md` where relevant and `README.md`. All earlier tests, convention tests and import-linter contracts keep passing. Comments only where intent is not obvious.

**Never:** reveal whether an address is registered (status, body, headers, 429, timing); store or log a plain token, password or hash; put a password or token in an email; end the current session on a password change; let a reset leave any session alive; let a validation failure consume a reset token; verify the email or change the account state through a reset; return 429 from the change route; add an audit record other than the two named; add a contact-data regex outside `patterns.py`; send mail inside a request transaction (always after commit through events or the mail port's after-commit queue).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Request, registered | existing Active or Unverified or Suspended email | 202; after commit a new token supersedes older ones and one email with the link is queued | N/A |
| Request, unknown or deactivated | | identical 202; nothing queued | N/A |
| Enumeration | compare responses across unknown, active, unverified, suspended, deactivated, capped | same status, body and headers apart from the request id | test |
| Client limit | 6th request in an hour from one client | 429 with `Retry-After`, for any address alike | envelope |
| Account cap | 6th request for one account from different clients | identical 202; no sixth email | N/A |
| Confirm ok | newest unexpired unused token, valid password | 200 `password_reset`; password changed; token used; all sessions ended; audit `account.password_reset`; session_ended emitted; notice queued after commit | N/A |
| Expired | after 1 hour | 410 `link_expired`; nothing changes | envelope |
| Used or superseded | reuse a token, or an older token after a newer one | 409 `link_used` | envelope |
| Unknown, malformed, oversized token | | 400 `link_invalid`, token not echoed | envelope |
| Bad password | valid link, weak password | 400 `validation_error` with `fields.password` code; token NOT consumed; the same link then works | envelope |
| Old password after reset | sign in with the old password | refused (generic 401); new password works | N/A |
| Concurrent confirm | many parallel confirms of one token | exactly one 200, others `link_used` (threaded, PostgreSQL) | N/A |
| Deactivated account link | token for a deactivated account | `link_invalid` | envelope |
| Change ok | signed in, right current password, valid new | 200 `password_changed`; hash replaced; other sessions ended, current stays valid; audit `account.password_changed`; event with `except_session_id`; notice queued | N/A |
| Change, wrong current | | 400 `fields.current_password=incorrect`; nothing changes | envelope |
| Change throttle | 5 wrong attempts then the correct current password | all `incorrect` during the window; clears after it; no 429 | test with clock |
| Change, same password | new equals current | 400 `fields.new_password=password_same_as_current` | envelope |
| Change, weak new | policy failure | 400 with the policy code | envelope |
| Change, not signed in or no CSRF | | 401 `unauthenticated` / 403 `forbidden` | envelope |
| Other sessions | account with three sessions, change from one | the other two end; the current continues; the account's other browser gets 401 | N/A |
| Privacy | logs, audit metadata, errors, events, emails other than the link | no token, hash, password, email (canary) | test |
| Storage | reset token row | hash only | test |
| Retention | `run_retention` | old tokens purged; dry run counts | N/A |
| Failure | mail backend raises in the handler | logged by class; responses unaffected; user can request again | N/A |
| Rollback | failure after hashing inside confirm | password unchanged, token unused, sessions untouched | N/A |
| Contract | `openapi.yaml` | three routes, field messages, freshness passes | test |

</frozen-after-approval>

## Code Map

- `apps/identity/models.py`, `migrations/0005_password_reset_token.py` -- `PasswordResetToken`
- `apps/identity/services.py`, `notices.py`, `events.py`, `subscribers.py`, `apps.py`, `selectors.py` -- request, confirm, change, notices, event field and handler, audit actions, retention, session exclusion
- `apps/kernel/migrations/0007_seed_password_policies.py` -- `password_reset_request`, `password_change_failed`; `apps/kernel/errors.py` -- field messages
- `apps/presentation/api/passwords.py` (views and serializers), `authentication.py` (identity carries the session id), `config/urls.py`
- `openapi.yaml`, `docs/authorisation-matrix.md` regenerated
- `tests/identity/test_password_reset.py`, `tests/api/test_password_api.py`, seed and retention tests -- every matrix row
- `docs/password-reset.md`, `docs/sessions.md`, `docs/rate-limits.md`, `README.md`

## Tasks & Acceptance

**Execution:**
- [ ] token model, request and confirm services, notices, handler, retention
- [ ] change service, throttle, session exclusion, event field, audit
- [ ] endpoints, seeds, field messages, regenerated contracts
- [ ] tests covering every matrix row; docs

**Acceptance Criteria:**
- Given the reset form, when I submit any email, then the response is identical whether or not it is registered, and a registered address receives a single-use link that expires after 1 hour.
- Given a valid reset link, when I set a new password meeting the policy, then the password changes and the link cannot be reused, and all sessions of the account end and `account.session_ended` is emitted.
- Given I am signed in, when I change my password with my current password, then my other active sessions end and the change is recorded in the audit trail.
- Given reset emails exceed the limit, when another is requested, then the 429 envelope is returned (per client, see the interpretation above).

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, backend half (blind, edge-case, verification-gap).

| # | Finding | Verdict | Route |
|---|---------|---------|-------|
| 1 | A password change left outstanding reset links valid (blind, edge) | high | patch: change marks every unused reset token used |
| 2 | Known vs unknown addresses did different response-path DB work, docstring wrong (blind, edge) | medium | patch: symmetric counter write; after-commit and SMTP timing residual documented |
| 3 | Delayed `session_ended` handler could end a session opened after commit (blind, edge) | medium | patch: `ended_before` bound on event and handler |
| 4 | Confirm route had no throttle (blind, edge) | medium | patch: seeded `password_reset_confirm` 20 per hour per client |
| 5 | Unusable-password accounts could be re-enabled by a reset (blind) | low | patch: treated as unknown |
| 6 | Notice copy wrong for reset, seed rollback could delete tuned policies, duplicated body helpers, purge filter (blind, edge) | low | patch |
| 7 | Tests: confirm-side supersede, change throttle success path, state unchanged after reset, missing or non-string password, audit actor, seed idempotency, no-store on errors (verification-gap) | medium | patch: about 20 tests |
| 8 | Silent per-account cap lets an attacker block a victim's reset for an hour; a new request supersedes an already-emailed link (blind, edge) | medium | defer, same accepted risk as Story 1.16 #9, documented |
| 9 | Token in query string rather than fragment; hashing under lock; fail-open throttle on store faults (blind, edge) | low | defer, consistent with Stories 1.16 and 1.17 |

Deviation recorded: in `change_password`, `ended_before` and the in-transaction end use a fresh `now` taken after the locks and hash, so a session created while waiting for the lock is not missed.

## Design Notes

Decisions made without asking: reset and verification share the token helpers but live in separate tables so their lifetimes and meanings stay independent; the story's per-account 429 is interpreted as per client because a per-account 429 would reveal existence, the per-account cap stays silent as in Story 1.16; Suspended accounts may reset (they can sign in); a reset does not verify the email; sessions end inside the transaction rather than only through the after-commit event, which resolves the Story 1.17 accepted risk; a validation failure leaves the token usable so a typo does not burn the link; the change route throttles wrong current passwords silently instead of with 429.

## Verification

**Commands (backend):**
- `uv run python manage.py migrate --database=migrator`
- `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run pytest -q && uv run python manage.py makemigrations --check --dry-run`
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- regenerate and commit
