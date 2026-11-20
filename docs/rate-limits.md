# Rate limits

Limits live in one table, `kernel_ratelimitpolicy` (`apps/kernel/models.py`), seeded by migration
`0002_seed_rate_limit_policies` with every NFR-10 default. Edit rows to retune; code never hardcodes a limit.
Counters live in `kernel_ratelimitcounter` (PostgreSQL, no Redis).

| Scope | Limit | Window | Key | Applied by |
|---|---|---|---|---|
| `registration` | 10 | 1 hour | client | registration stories |
| `verification_email` | 5 | 1 hour | account | verification story (shared by the resend and the existing-address notice); Story 1.20 also counts email-change mail per requesting account and per target address (key `address`) |
| `password_reset_email` | 5 | 1 hour | account | password reset (Story 1.18); silent per-account cap; also counts unknown and ineligible addresses under an address key |
| `messages` | 60 | 1 minute | account | messaging |
| `conversations_new` | 20 | 1 day | account | messaging |
| `favourites` | 500 | cap | account | favourites |
| `reports` | 10 | 1 day | account | reporting |
| `support_requests` | 5 | 1 day | account | support |
| `open_drafts` | 20 | cap | account | listings |
| `assistant_messages` | 30 | 1 hour | account | assistant |
| `ai_search_visitor` | 10 | 1 minute | client | search |
| `ai_search_account` | 30 | 1 hour | account | search |
| `estimates` | 20 | 1 hour | account | valuation (FR-42) |
| `verification_resend` | 5 | 1 hour | client | `POST /api/v1/email-verification-requests` (Story 1.16); 429 for every address alike |
| `password_reset_request` | 5 | 1 hour | client | `POST /api/v1/password-reset-requests` (Story 1.18); 429 for every address alike; the per-account `password_reset_email` cap is silent; see `docs/password-reset.md` |
| `password_reset_confirm` | 20 | 1 hour | client | `POST /api/v1/password-resets/confirm` (Story 1.18); 429 with `Retry-After` |
| `password_change_failed` | 5 | 15 minutes | account | `POST /api/v1/me/password` (Story 1.18) and the current-password check of `POST /api/v1/me/email-change` (Story 1.20), one shared counter; failures only; at the cap the same `incorrect` error is returned, never 429 |
| `email_change_request` | 5 | 1 hour | account | `POST /api/v1/me/email-change` (Story 1.20); only requests with the right password count; 429 with `Retry-After`; the wrong-password check itself uses `password_change_failed` and never answers 429 |
| `email_change_confirm` | 20 | 1 hour | client | `POST /api/v1/email-changes/confirm` (Story 1.20); 429 with `Retry-After` |
| `sign_in` | 5 | 15 minutes | client | `POST /api/v1/session` (Story 1.17); keyed by lowercased email plus client network; failures only; see `docs/sessions.md` |

A null window marks a cap: `limit_for(scope)` returns it for the owning story to count against; `hit` refuses it.
Each owning story attaches its scope.

## Semantics

- **Fixed windows.** Time is cut into windows of `window_seconds`; the count restarts at each boundary. A client can
  burst up to twice the limit across a boundary. Accepted for the prototype.
- **Atomic.** One `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` per hit; no read-then-write. Hits over the limit
  still count, and `retry_after` is the seconds to the window end.
- **Client key** (`kernel.ratelimit.client_key(actor, request, key_kind)`), chosen by the policy's `key_kind`:
  `account` uses the account id and refuses a Visitor (`ImproperlyConfigured`); `client` always uses the client address
  from `kernel.network.client_ip` (forwarded headers honoured only from `TRUSTED_PROXIES`) plus the session key (empty:
  the app has no Django session, so it is keyed by address only), even for signed-in actors, so signing in does not escape it. IPv6 addresses are
  keyed by their /64, so one subscriber cannot rotate through addresses in its prefix. The key is an HMAC under
  `SECRET_KEY`; no raw address, session or id is stored. Use `derive_key(*parts)` for composite keys such as account
  plus address.
- **Bounded counters.** The stored count is capped at `limit + 1` in the same upsert, so refused hits do not grow it.
- **Shared addresses.** Visitors behind one address (an office, a mobile carrier) with no session share a counter and
  can throttle each other. This is best-effort by design (NFR-10); signed-in accounts are unaffected.
- **Fail open.** If the counter statement fails, the request is allowed and only the exception class is logged
  (`ratelimit_store_failure`). A database outage already fails the request; a throttle fault must not lock users out.
- **Unknown scope** raises `ImproperlyConfigured`; a scope is never silently unlimited.
- **Purge.** `kernel.ratelimit.purge_expired(now)` deletes counters whose window has ended; the retention runner
  (Story 1.9) will call it.

## Operating notes

- **Editing policies.** Only through a migration or a controlled SQL change run by the `migrator` role; the `runtime`
  role can read policies but a request never edits them. There is no admin UI yet.
- **Rotating `SECRET_KEY`** changes every key hash, which resets all counters (old rows are purged once their windows end).
- **Sign-in counts failed attempts only.** `ratelimit.peek(scope, key, now=None)` reports whether the key has reached its
  limit in the current window without counting; sign-in peeks first, then calls `hit` on a failure. A success does not
  reset the counter (the window clears it) and the route never uses 429, so lockout reveals nothing. A cap scope or an
  unknown scope raises `ImproperlyConfigured`; a store fault fails open like `hit`. `ratelimit.client_network(request)`
  gives the trusted client network (IPv6 as /64) for composite keys built with `derive_key`.

## Adopting a scope

In a view: set `throttle_classes = [PolicyThrottle]` (`apps/presentation/api/throttling.py`) and
`rate_limit_scope = "<scope>"`. Over the limit the response is 429 in the standard envelope (code `throttled`) with
`Retry-After`. In a service: `kernel.ratelimit.enforce(scope, key)` raises `RateLimited(retry_after)`, which the
API handler renders the same way. Add a new scope by migration (a new policy row).
