---
title: 'Story 1.17: Sign-in and sign-out with session security (backend half)'
type: 'feature'
created: '2026-11-19'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-16-email-verification.md'
  - '{project-root}/docs/account-states.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Accounts exist and can be verified, but nobody can sign in: there is no session, no authentication, and every route still resolves the caller as a Visitor.

**Approach:** Add server-side sessions in `identity` (random token in an HttpOnly cookie, only its hash stored), a DRF authentication class that turns a valid session into the request's authenticated identity (the existing `request.authenticated_identity` hook in `presentation/api/actors.py`), CSRF protection for cookie-authenticated and sign-in requests, sign-in with a generic error and a failure throttle, sign-out, and `GET /api/v1/me`. This spec is the backend half; the frontend half is a second spec written after the backend merges.

## Boundaries & Constraints

**Always:**
- Model `Session` in `identity` (UUID pk, FK `Account` CASCADE, `token_hash` SHA-256 hex unique, `created_at`, `last_seen_at`, `expires_at`, `ended_at` nullable, `ended_reason` blank or one of `sign_out`, `password_changed`, `deactivated`, `replaced`); the plain token is `secrets.token_urlsafe(32)`, shown only in the cookie, never stored or logged. Written only by `identity` services; one migration; runtime role DML through the existing hook.
- Lifetimes (settings, defaults): absolute 14 days, idle 24 hours; `last_seen_at` is refreshed at most once per 5 minutes; a session past either limit or with `ended_at` set is invalid.
- Cookie `estate_session`: HttpOnly, SameSite=Lax, `Secure` follows the existing `SESSION_COOKIE_SECURE` (false only in local settings), path `/`, `Max-Age` equal to the absolute lifetime. Responses that set or clear it carry `Cache-Control: no-store`. The session is never exposed in a body.
- **Sign-in** `POST /api/v1/session` body `{"email","password"}` (strict serializer, same single-shape discipline as 1.16), public. Email is trimmed and matched case-insensitively. Success: `200` with the actor summary below, a brand-new session (fixation defence: any session cookie the request already carried is ended with reason `replaced`), cookie set. Wrong password, unknown email and a Deactivated account all return the identical `401 invalid_credentials` (new catalogue code, generic message) with identical headers and body; a missing or non-string field is `400 validation_error`. Unknown emails and Deactivated accounts still run a password hash comparison against a fixed dummy hash so timing does not distinguish them. Email-unverified and Suspended accounts CAN sign in and receive their derived state in the summary (restricted by the capability matrix, FR-74); Deactivated cannot.
- **Failure throttle** (policy `sign_in`, already seeded: 5 per 900 s): keyed by `derive_key("sign_in", lowercased trimmed email, client network part)`. Only failed attempts count. Before checking the password the service asks whether the key has reached its limit (a new non-incrementing `ratelimit.peek(scope, key)` returning whether the limit is reached in the current window); if so it returns the same `401 invalid_credentials` WITHOUT verifying the password (so a correct password during lockout also fails) and without incrementing. A failure increments through `ratelimit.hit`. A success does not reset the counter (the window clears it). The throttle never uses 429 or `Retry-After` on this route, so it reveals nothing. Per-address keying means one attacker cannot lock an account from a different network, accepted and documented.
- **Authentication**: `apps/presentation/api/authentication.py` defines `SessionAuthentication` (DRF `BaseAuthentication`) added to `DEFAULT_AUTHENTICATION_CLASSES`: it reads the cookie, asks an `identity` selector for the live session (valid, not ended, within limits, account exists and is not Deactivated), refreshes `last_seen_at` per the rule above, sets `request.authenticated_identity` (kind `admin` for account type admin, else `user`; `account_id`), and returns `(None, session_marker)` or equivalent so DRF treats the caller as authenticated without a Django user model. An invalid or unknown cookie yields a Visitor, never an error. Authentication classes on existing public views (`authentication_classes = []`) stay as they are; `DEFAULT_AUTHENTICATION_CLASSES` is changed only if the authz-matrix tests and every existing route behave identically for Visitors. `UNAUTHENTICATED_USER` stays `None`.
- **CSRF**: Django's `CsrfViewMiddleware` stays; DRF views are exempt from it by default, so `SessionAuthentication.authenticate` enforces it (the DRF `enforce_csrf` pattern) for unsafe methods whenever a session cookie authenticated the request, and sign-in enforces it explicitly even for Visitors (login CSRF). `GET /api/v1/csrf` (public, `Cache-Control: no-store`) returns `200 {"csrf_token": "<token>"}` and sets the CSRF cookie; because `CSRF_COOKIE_HTTPONLY` is true, the SPA cannot read the cookie and must fetch the token from this route and send it as `X-CSRFToken`. A missing or wrong token on a protected request returns the standard envelope `403 forbidden` (the existing `csrf_failure` view). `CSRF_TRUSTED_ORIGINS` is derived from `FRONTEND_BASE_URL` so requests through the frontend proxy pass the origin check. Safe methods never need a token.
- **Sign-out** `DELETE /api/v1/session`, signed-in (`RequireSignedIn`), CSRF enforced: ends only the current session (`ended_at`, reason `sign_out`), clears the cookie, emits `account.session_ended` with reason `sign_out` through the existing `emit_session_ended`, returns `204`. A call without a valid session is `401 unauthenticated`. Calling it twice: the second is `401`.
- **Ending sessions by event**: a subscriber (registered in `identity`'s `subscribers.py` through `AppConfig.ready`, subscribe-location rule respected) for `account.session_ended` ends ALL of the account's open sessions when the reason is `password_changed` or `deactivated` and does nothing for `sign_out`. The existing `deactivate` service must result in the account's sessions ending (emit the event with reason `deactivated` if it does not already, inside its transaction) and deactivated accounts are also refused at authentication time, so there is no window.
- **Me** `GET /api/v1/me`, signed-in, returns `200` the actor summary `{id, account_type, state, email, full_name, capabilities}` where `state` is the derived account state, `email` is the account's own email (private data, only for the owner, `Cache-Control: no-store`), `full_name` is the Individual profile name or empty, and `capabilities` is the list for that state and type from the existing `identity/capabilities.py` matrix (use what that module exposes; do not invent new rules). Visitors get `401 unauthenticated`.
- Passwords are checked with Django's `check_password`; the hash is never returned or logged. No log record, audit metadata, error body or event payload contains the password, the email, the session token or its hash (events carry only the account id and reason). A failed or throttled sign-in writes no audit record; no audit record is added by this story.
- Retention job `identity.sessions` registered through `kernel.retention.register`: sessions ended or expired more than 7 days ago are purged (indexes on the columns it filters).
- `openapi.yaml` and `docs/authorisation-matrix.md` are regenerated; new docs `docs/sessions.md` (lifetimes, cookie, CSRF flow for the frontend, throttle, enumeration rule, event handling) and updates to `docs/rate-limits.md`, `docs/account-states.md`, `README.md`; the hand-off for the frontend half is written. All earlier tests, convention tests and import-linter contracts keep passing. Comments only where intent is not obvious.

**Never:** store the plain session token; return the session token in any body; use Django's `django.contrib.sessions` or `django.contrib.auth` user model; reveal through status, body, headers or timing whether an email is registered (including during lockout); let a throttle use 429 on sign-in; read any client header or body value to decide the actor (only the verified session); accept CSRF-less unsafe requests on a cookie-authenticated or sign-in route; let a Deactivated account hold a working session; add an audit record; put the CSRF token in a non-`no-store` cacheable response.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Sign-in ok | verified account, right password, valid CSRF | 200 summary; new session row (hash only); cookie flags as specified | N/A |
| Sign-in unverified | email-unverified account, right password | 200, `state: email_unverified` | N/A |
| Sign-in suspended | suspended account | 200, `state: suspended` | N/A |
| Sign-in deactivated | deactivated, right password | identical 401 `invalid_credentials` | envelope |
| Wrong password / unknown email | either | identical 401 body, headers and status | envelope |
| Timing | unknown vs known email | both run one password hash comparison | test counts hasher calls |
| Throttle | 5 failures for one email from one client, then a 6th (wrong), then the correct password | all 401 `invalid_credentials`; the correct password during lockout fails; clears after the window (clock-controlled) | N/A |
| Throttle isolation | failures for one email, another email or another client network | unaffected | N/A |
| Session fixation | sign-in with an existing valid session cookie | old session ended `replaced`; new token differs | N/A |
| Missing / bad CSRF | sign-in or sign-out without or with a wrong token | 403 `forbidden` envelope; no session created or ended | envelope |
| CSRF fetch | `GET /api/v1/csrf` | 200 token, CSRF cookie set, `no-store` | N/A |
| Me | valid session | 200 summary incl. capabilities; `no-store` | N/A |
| Me, no/invalid/expired/ended cookie | | 401 `unauthenticated` | envelope |
| Idle and absolute expiry | clock past 24 h idle or 14 d | session invalid, 401 | N/A |
| Sliding refresh | request within 5 min of `last_seen_at` | `last_seen_at` unchanged; later request updates it | N/A |
| Sign-out | valid session + CSRF | 204, cookie cleared, session ended `sign_out`, event emitted; later `me` is 401; other sessions of the account stay valid | N/A |
| Sign-out twice | | second is 401 | envelope |
| Password-changed / deactivated event | `account.session_ended` with those reasons | every open session of the account ended | N/A |
| Deactivation | deactivate an account with live sessions | those sessions stop working immediately | N/A |
| Suspended account session | existing session when suspended | still valid; summary shows `suspended` | N/A |
| Storage | session row | holds the hash only | test |
| Privacy | logs, errors, events | no password, token, hash or email (canary test) | test |
| Visitor routes | every existing public route with no cookie | behaviour unchanged | existing tests |
| Contract | `openapi.yaml` | session, csrf and me routes, `invalid_credentials` in the code enum, freshness passes | test |
| Retention | `run_retention` | old ended or expired sessions purged; dry run counts only | N/A |
| Concurrency | many parallel failed sign-ins | counter never exceeds the cap; no 500 | threaded test |

</frozen-after-approval>

## Code Map

- `apps/identity/models.py`, `migrations/0004_session.py` -- `Session`
- `apps/identity/sessions.py` (or inside `services.py` per the actor-first convention test) -- start, resolve, end, end-all, purge
- `apps/identity/services.py`, `selectors.py`, `subscribers.py`, `apps.py` -- `sign_in`, `sign_out`, session selector for authentication, event handler, retention registration, deactivation behaviour
- `apps/kernel/ratelimit.py` -- `peek`; `apps/kernel/errors.py` -- `invalid_credentials`
- `apps/presentation/api/authentication.py`, `sessions.py` (views), `actors.py` (docstring), `config/settings/base.py` (auth class, lifetimes, CSRF trusted origins), `config/urls.py`
- `openapi.yaml`, `docs/authorisation-matrix.md` regenerated
- `tests/identity/test_sessions.py`, `tests/api/test_session_api.py`, kernel peek tests -- every matrix row
- `docs/sessions.md`, `docs/rate-limits.md`, `docs/account-states.md`, `README.md`

## Tasks & Acceptance

**Execution:**
- [ ] session model, lifecycle services, retention job
- [ ] ratelimit peek, error code, sign-in and sign-out services, event handler, deactivation
- [ ] authentication class, CSRF enforcement, settings, endpoints, regenerated contracts
- [ ] tests covering every matrix row; docs

**Acceptance Criteria:**
- Given valid credentials, when I sign in, then a new session is issued in an HttpOnly, SameSite=Lax cookie (Secure outside local) with CSRF protection, and `GET /api/v1/me` returns my actor summary.
- Given wrong credentials or an unknown email, when I sign in, then the same generic error is returned and does not reveal whether the email exists.
- Given 5 failed sign-ins within 15 minutes for one account and address, when I try again, then the same generic message is returned and the throttle clears when the window ends.
- Given a signed-in session, when I sign out, then the session is invalidated and authenticated calls are rejected until I sign in again.
- Given a Deactivated account, when it tries to sign in, then sign-in is refused; a Suspended account can sign in with restricted capabilities; sign-out emits `account.session_ended`, and a password change also ends sessions through the same event.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, backend half (blind, edge-case, verification-gap).

| # | Finding | Verdict | Route |
|---|---------|---------|-------|
| 1 | Throttle check-then-act race let parallel guesses all pass the peek; failure count rolled back on the deactivated-race branch (all three) | high | patch: per-key advisory lock around peek, verify and count; raise after the block exits |
| 2 | Session minted from a stale password after a concurrent password change (blind) | high | patch: recheck hash and email after the row lock |
| 3 | No CSRF rotation on sign-in and sign-out (blind, edge) | medium | patch: `rotate_token`; frontend refetches the token |
| 4 | Timing outliers: lazy dummy hash, unusable password hashes (blind, edge) | medium | patch: warm at start-up, unusable treated as unknown |
| 5 | Unbounded sessions per account (blind, edge) | medium | patch: cap of 10, oldest ended `replaced` |
| 6 | Cookie not cleared on sign-out 401; failed touch could 500 a read; refresh ignored expiry; unvalidated session settings; trailing-slash origins; end reasons defined in four places (edge, blind) | low | patch |
| 7 | Tests did not prove network keying, trusted-origin derivation, concurrency count, cookie-clear attributes, public POST routes with a cookie, deactivated race, retention run (verification-gap) | medium | patch: added, 22 tests |
| 8 | No audit records for sign-in or sign-out (blind) | low | reject: the spec forbids them (private data, no requirement) |
| 9 | No global or per-account sign-in throttle; fixed-window burst; shared-key lockout when `TRUSTED_PROXIES` is unset (blind, edge) | medium | defer, documented in `docs/sessions.md` accepted risks |
| 10 | `__Host-` cookie prefix, hash upgrade on sign-in, purge batching, email casefolding, `password_changed` emitter must end sessions in its own transaction (blind, edge) | low | defer to later stories, documented |

Deviation recorded: the strengthened concurrency test asserts exactly 5 failures counted, since serialised attempts stop at the limit.

## Design Notes

Decisions made without asking: server-side sessions with a hashed token rather than signed cookies, so sign-out and deactivation take effect immediately; the throttle returns the generic 401 instead of 429 so lockout state cannot be used to probe accounts; sign-out ends only the current session while password change and deactivation end all of them; the CSRF token is fetched from an endpoint because the CSRF cookie is HttpOnly; Email-unverified accounts can sign in because FR-74 restricts them by capability rather than by refusing entry; no audit record for sign-in because the story does not ask for one and a successful-login log would add private data.

## Verification

**Commands (backend):**
- `uv run python manage.py migrate --database=migrator`
- `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run pytest -q && uv run python manage.py makemigrations --check --dry-run` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- expected: files regenerated and committed
