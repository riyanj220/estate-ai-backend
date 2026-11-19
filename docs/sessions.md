# Sessions, sign-in and CSRF (Story 1.17)

Server-side sessions in `identity`. The browser holds a random token in an HttpOnly cookie; the database holds only its
SHA-256 hash, so a database read cannot be replayed as a login and sign-out takes effect at once.

## Endpoints

| Route | Who | Purpose |
|---|---|---|
| `GET /api/v1/csrf` | public | Sets the CSRF cookie and returns `{"csrf_token": "..."}` (`Cache-Control: no-store`). |
| `POST /api/v1/session` | public, CSRF | Sign in with `{"email", "password"}`. `200` with the actor summary and the session cookie. |
| `DELETE /api/v1/session` | signed in, CSRF | Sign out: ends only the current session, clears the cookie, `204`. |
| `GET /api/v1/me` | signed in | The actor summary (`Cache-Control: no-store`). |

The actor summary is `{id, account_type, state, email, full_name, capabilities}`. `state` is the derived account state,
`email` is the account's own address (private: only the owner ever sees it), `full_name` is the Individual profile name
or empty, and `capabilities` is what `identity.policy.can` grants that state and type (`identity.policy.capabilities_for`;
capabilities whose features have not shipped are not listed). The session token and its hash never appear in a body.

`/api/v1/session` is one route with two methods, so the authorisation matrix shows it as `public`; `DELETE` is gated by the
sign-out service (`unauthenticated` without a session) and by the CSRF check in the authentication class.

## Session lifecycle

- **Token**: `secrets.token_urlsafe(32)`, shown only in the cookie, never stored, never logged.
- **Cookie** `estate_session`: HttpOnly, SameSite=Lax, path `/`, `Secure` follows `SESSION_COOKIE_SECURE` (false only in local
  and test settings), `Max-Age` equal to the absolute lifetime. Responses that set or clear it are `no-store`.
- **Lifetimes** (settings, env overridable): absolute `SESSION_ABSOLUTE_SECONDS` = 14 days from sign-in, idle
  `SESSION_IDLE_SECONDS` = 24 hours since `last_seen_at`. `last_seen_at` is refreshed at most once per
  `SESSION_TOUCH_SECONDS` = 5 minutes. A session past either limit, or with `ended_at` set, is invalid.
- **Fixation**: sign-in always creates a new session; a session cookie the request already carried is ended with reason
  `replaced`.
- **Open-session cap**: at most `SESSION_MAX_OPEN` (default 10) open sessions per account; a new sign-in beyond it ends the
  oldest as `replaced`. `replaced` is internal and never announced as an event.
- **Settings check**: at load, `0 < SESSION_TOUCH_SECONDS < SESSION_IDLE_SECONDS <= SESSION_ABSOLUTE_SECONDS` and
  `SESSION_MAX_OPEN > 0`, or start-up fails. The end reasons are defined once (`identity.models.SESSION_END_REASONS`).
- **Ending**: `sign_out` ends the current session only. A `password_changed` or `deactivated` `account.session_ended` event
  (handler in `identity/subscribers.py`) ends every open session of the account, except `except_session_id` when the event
  carries one (a password change keeps the session it came from); `sign_out` events end nothing more. A
  Deactivated account is also refused at authentication time, so there is no window before the event runs. A Suspended
  account keeps its session; it is restricted by capability.
- **Retention**: job `identity.sessions` purges sessions ended, expired or idled out more than 7 days ago.
- No audit record is written by sign-in or sign-out, and a failed attempt writes nothing: a successful-login log would hold
  private data.

## Authentication

`apps/presentation/api/authentication.py` defines `SessionAuthentication` (the only default DRF authentication class). It reads
the cookie, asks `identity.selectors.live_session` for the live session, refreshes `last_seen_at`, and sets
`request.authenticated_identity` (kind `admin` for an admin account, else `user`), which `actors.resolve_actor` turns into the
Actor. An unknown, expired or ended cookie is a Visitor, never an error. No header or body value decides the actor. Views with
`authentication_classes = []` (registration, verification, CSRF) ignore the cookie. Visitors on any route behave as before
(`DenyByDefault` raises `forbidden` itself, so a refused Visitor is still 403 rather than DRF's 401).

## Sign-in rules

- The strict body is exactly `email` and `password`, both strings; anything else is `400 validation_error`. The email is
  trimmed and matched case-insensitively.
- Wrong password, unknown email, a Deactivated account and a throttled attempt all return the same
  `401 invalid_credentials` (generic message) with the same headers and body. Unknown emails and Deactivated accounts still
  check the password against a dummy hash, so timing does not separate them.
- Email-unverified and Suspended accounts sign in and receive their state (FR-74 restricts them by capability).
- Over-long (more than 1024 characters) passwords, empty or over-long emails are refused as `invalid_credentials` without
  hashing.

### Failure throttle

Policy `sign_in` (5 per 900 s), keyed by `derive_key("sign_in", lowercased trimmed email, client network)` (IPv6 as /64).
Only failures count. Before verifying, the service calls `ratelimit.peek(scope, key)` (never increments): when the limit is
reached it returns the same `invalid_credentials` without checking the password, so a correct password during lockout also
fails and the counter does not grow. A failure calls `ratelimit.hit`. A success does not reset the counter; the fixed window
clears it. The route never answers `429` or `Retry-After`, so lockout reveals nothing about the account. The key includes the
client address, so an attacker on another network cannot lock an account out; the cost is that an attacker with many addresses
is limited per address only. Attempts for one key run one at a
time (a transaction-scoped PostgreSQL advisory lock on a hash of the key around peek, verify and the failure count), so
parallel guesses cannot all pass the peek; the failure count is committed even though the attempt fails.
After the password check the service locks the account row and re-checks that the password hash and email are unchanged;
otherwise the attempt fails (no session from a stale password). Accounts whose stored password is unusable are checked
against the dummy hash like unknown emails, and the dummy hash is computed at start-up, not on a first request.

## CSRF

Django's `CsrfViewMiddleware` stays on. DRF views are exempt from it by default, so:

- `SessionAuthentication` enforces it for unsafe methods whenever a session cookie authenticated the request;
- sign-in enforces it explicitly even for Visitors (login CSRF).

`CSRF_COOKIE_HTTPONLY` is true, so a script cannot read the cookie. The frontend must call `GET /api/v1/csrf` and send the
returned token as `X-CSRFToken` on `POST /api/v1/session` and `DELETE /api/v1/session` (and on any later unsafe request made
with a session cookie). A missing or wrong token, or an untrusted `Origin`, is the standard `403 forbidden` envelope; safe
methods never need a token. `CSRF_TRUSTED_ORIGINS` always includes `FRONTEND_BASE_URL`, so requests through the frontend proxy
pass the origin check.

## Frontend hand-off

1. On app start (and after a `403 forbidden` on an unsafe request) call `GET /api/v1/csrf`; keep the token in memory only.
2. Sign in with `POST /api/v1/session` (`X-CSRFToken`, JSON `{"email","password"}`), same-origin through the Next.js proxy so
   the cookie is first-party. `200` returns the summary; the cookie is set by the response. `401 invalid_credentials` shows
   one generic message whatever the cause; do not distinguish or count attempts on the client.
3. Fetch the token again after sign-in and after sign-out: the server rotates the CSRF secret on both (`rotate_token`), so
   the previous token stops working and `GET /api/v1/csrf` returns the new one.
4. Restore the session on load with `GET /api/v1/me`: `200` is signed in, `401 unauthenticated` is a Visitor.
5. Drive restricted UI from `state` and `capabilities`, never from guesses; `email_unverified` and `suspended` accounts are
   signed in but restricted.
6. Sign out with `DELETE /api/v1/session` (`X-CSRFToken`); `204` means the session is gone. A `401` from sign-out also
   clears the cookie, so a stale cookie cannot linger. Any later `401` on an
   authenticated call means the session ended (expiry, deactivation, password change): return to the signed-out state.
7. Never read, store or send the session cookie from script; it is HttpOnly.

## Accepted risks and follow-ups

- **Fixed-window burst**: the failure throttle uses fixed windows, so up to twice the limit can land across a boundary.
- **No per-account or global sign-in throttle**: the key is email plus client network. An attacker with many addresses is
  limited per address only; an attacker on one address cannot lock out an owner on another.
- **`TRUSTED_PROXIES` must be set behind the proxy**: otherwise every client appears to come from the proxy address and all
  users share one throttle key (and one lockout).
- **No audit by design**: sign-in and sign-out write no audit record (private data, noise); failures are not logged.
- **`password_changed` emitters (resolved in Story 1.18)**: password reset and password change end sessions directly in
  their own transaction and then emit `account.session_ended` (reason `password_changed`, with `except_session_id` for a
  change), so a crash between commit and dispatch cannot leave old sessions alive. See `docs/password-reset.md`.
- **No hash upgrade on sign-in**: a password hashed with an older algorithm or cost is not re-hashed when it verifies.
- **No `__Host-` cookie prefix**: `estate_session` is not prefixed. Moving to `__Host-estate_session` (Secure, path `/`, no
  Domain) is a documented follow-up; it needs `Secure` in local development.
