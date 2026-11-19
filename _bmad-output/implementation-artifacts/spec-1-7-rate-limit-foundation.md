---
title: 'Story 1.7: Rate-limit foundation'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '17bb3cf50f21d0e537cd925e797a8da483aeb5c7'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-6-actor-and-default-deny-authorisation.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing limits how often a client may register, sign in, search or send messages, so abuse and runaway cost are unchecked, and there is no shared place where limits are defined.

**Approach:** Add the first kernel tables (a policy table seeded with every NFR-10 limit and a counter table) with fixed-window counters persisted in PostgreSQL, a client-key function (account, or trusted-proxy address plus session for Visitors), a `kernel.ratelimit` API for services, and a DRF throttle that answers 429 in the standard error envelope with a retry hint. This story applies limits to no real endpoint; each owning story attaches its own scope.

## Boundaries & Constraints

**Always:** `kernel` becomes an installed Django app (label `kernel`) holding two models with UUID primary keys and plain-UUID or string references only (no foreign keys to other apps); migrations run as the `migrator` role and the existing post-migrate grant gives the `runtime` role DML only; counter increments are atomic in one SQL statement (`INSERT ... ON CONFLICT DO UPDATE ... RETURNING`), never read-then-write; the counter key never stores a raw address or session value (HMAC keyed by `SECRET_KEY`); the client address comes only from `kernel.network.client_ip` so forwarded headers from untrusted sources are ignored; a limited response is the Story 1.3 envelope with code `throttled`, status 429 and a `Retry-After` header; policy rows are the single source of limits, none hardcoded in code except the seed; Visitors and accounts are keyed separately; window kind is fixed window, documented; if the rate-limit store itself fails the request is allowed and the exception class is logged (fail open, documented); import-linter contracts, `openapi.yaml` freshness and the authorisation matrix keep passing; comments only where intent is not obvious.

**Never:** apply limits to real endpoints, build sign-in or registration, add Redis or any cache service, store raw IP addresses, session ids or emails, add the AI capacity budget or reserved partition (Story 1.9), build the retention runner (Story 1.9), or change the error envelope format.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Seed | after migrate | one policy row per NFR-10 limit with scope, limit, window seconds (null for caps), per-key kind and note; defaults match NFR-10 (estimates 20 per hour per account per FR-42; sign-in 5 per 900 s per FR-3) | a test lists every expected scope |
| Under limit | hits up to the limit in one window | allowed | N/A |
| Over limit | the next hit in the same window | refused with `retry_after` seconds to the window end | 429 envelope with `Retry-After` |
| New window | first hit after the window ends | allowed, count restarts | N/A |
| Concurrent hits | many parallel hits on one key | exactly `limit` allowed, no lost or double counts | proven by a threaded test against PostgreSQL |
| Account client | signed-in actor | key derived from the account id | separate accounts have separate counters |
| Visitor client | Visitor with address and session | key derived from trusted address plus session | HMAC only, no raw values stored |
| Spoofed header | `X-Forwarded-For` from an untrusted source | ignored, same key as without it | rotating the header cannot escape a limit |
| Trusted proxy | request from a configured proxy with a forwarded chain | client is the forwarded address | N/A |
| Unknown scope | scope not in the policy table | configuration error raised to the developer | never silently unlimited |
| Cap policy | scope with null window | returned by `limit_for(scope)` for the owning story to count against; not usable with `hit` | `hit` raises a clear error |
| Store failure | database error in the counter statement | request allowed, exception class logged, no detail | N/A |
| Runtime role | `runtime` role | can upsert counters and read policy; cannot run DDL | proven by the role test |
| Purge | counters whose window ended | `purge_expired(now)` deletes them | callable by the retention story later |

</frozen-after-approval>

## Code Map

- `apps/kernel/apps.py`, `apps/kernel/models.py`, `apps/kernel/migrations/0001_initial.py`, `0002_seed_rate_limit_policies.py` -- app config (name `apps.kernel`, label `kernel`), `RateLimitPolicy` (scope unique, limit, window_seconds nullable, key_kind account or client, note) and `RateLimitCounter` (scope, key_hash, window_start, count, unique together on the first three), idempotent seed migration
- `apps/kernel/ratelimit.py` -- `client_key(actor, request)`, `hit(scope, key)` returning a decision, `limit_for(scope)`, `purge_expired(now)`, `RateLimited` error carrying `retry_after`; atomic upsert via one SQL statement on the default connection
- `apps/presentation/api/throttling.py` -- DRF throttle reading `rate_limit_scope` from the view, using `get_actor` and `kernel.ratelimit`, raising the existing throttled path so the envelope and `Retry-After` come from the Story 1.3 handler (extend the handler only if needed)
- `config/settings/base.py` -- add `apps.kernel` to `INSTALLED_APPS`; check that the post-migrate grant covers the new tables and that `RUNTIME_APPEND_ONLY_TABLES` stays empty
- `tests/ratelimit/` -- seed, window, concurrency (threads, real PostgreSQL), key derivation and spoofed-header tests, throttle view in a tests-only urlconf returning the 429 envelope, store-failure test, purge test; extend `tests/test_db_roles.py` for the new tables
- `docs/rate-limits.md` -- the policy table, window semantics, shared-address caveat, fail-open rule, how an endpoint adopts a scope; `README.md` -- one line linking it

## Tasks & Acceptance

**Execution:**
- [x] `apps/kernel` app config, models, migrations and seed -- policy and counter tables
- [x] `apps/kernel/ratelimit.py` -- keys, atomic hit, limit lookup, purge
- [x] `apps/presentation/api/throttling.py`, settings -- DRF throttle and installed app
- [x] `tests/ratelimit/`, `tests/test_db_roles.py` -- cover every matrix row
- [x] `docs/rate-limits.md`, `README.md` -- documented policy and adoption steps

**Acceptance Criteria:**
- Given the policy table, when I read it, then limits are configurable by scope, window and limit, seeded with every NFR-10 row each applied by its owning story, and counters persist in PostgreSQL.
- Given a request from an authenticated account or a Visitor, when the client key is computed, then it is the account, or the trusted-proxy client address plus session for Visitors, and a forwarded address from an untrusted source is ignored (spoofed `X-Forwarded-For` test).
- Given a client over its limit, when it calls a limited endpoint, then the API returns 429 in the standard envelope with a retry hint, and the shared-address behaviour is documented in the policy.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | Service-path RateLimited to 429 and Retry-After untested at the API (BH, VG) | medium | patch | Handler branch never exercised; request-level test added. |
| 2 | `key_kind` stored but unused; account limits reach Visitors, signed-in users slip client limits (BH, EC, VG) | high | patch | Key now follows the policy kind; mismatches raise a configuration error. |
| 3 | Over-limit requests grow the counter without bound (BH) | medium | patch | Count capped at limit plus one in the same statement. |
| 4 | Policy read outside fail-open; naive `now` gives wrong windows (EC, BH) | medium | patch | Both handled with tests. |
| 5 | IPv6 /64 gives unlimited keys (BH) | medium | patch | Keyed by /64. |
| 6 | Purge has no index on window_start (BH) | low | patch | Index added. |
| 7 | Concurrency test truncates seeded policies; seed tests rely on ordering (EC, BH, VG) | medium | patch | Seed-dependent tests restore the seed. |
| 8 | Policy editing, key rotation, sign-in semantics undocumented (BH, EC) | low | patch | Documented. |
| 9 | sign_in counts every call; needs peek and reset (BH) | medium | defer | Belongs to Story 1.17. |
| 10 | Two queries per hit, no policy cache (BH) | low | reject | Accepted design decision, documented. |
| 11 | client_ip None, purge race, window retune, migration reverse, SECRET_KEY rotation, Retry-After wait (EC) | low | reject | Unlikely in everyday use; fix adds complexity. |
| 12 | Fail-open at HTTP level untested; CHECK constraints untested; no throttle log line (VG, BH) | low | reject | Thin wrapper over tested `hit`; metrics belong to observability work. |
| 13 | Runtime role can edit policy rows; no explicit GRANTs in migrations (BH) | low | reject | Intentional blanket DML via post-migrate grants; documented. |

## Design Notes

Decisions made without asking: fixed windows (simple, atomic; a burst up to twice the limit across a boundary is accepted for the prototype); counters keyed by HMAC so no raw address or session is stored; cap limits (favourites, open Drafts) live in the same policy table with a null window and are counted by their owning stories; fail open on store failure because the database being down already fails the request and a throttle outage must not lock users out; no policy cache (one small indexed read per hit); the visitor session component uses the session key when sessions exist (Story 1.17) and is empty until then; `kernel` is the first installed app, so the new migrations are the first schema beyond Django built-ins.

## Verification

**Commands:**
- `uv run python manage.py migrate --database=migrator` -- applies the new migrations
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && git diff --exit-code openapi.yaml` -- expected: no diff
