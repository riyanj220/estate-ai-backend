---
title: 'Story 1.8: Domain events and idempotency'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '857ce6ea84c0a22195f2f98ab1064a359f888db8'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-7-rate-limit-foundation.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Features need a safe way to react to committed changes (notifications, estimates, socket closes) without coupling apps or acting on rolled-back work, and clients need to retry mutations without creating duplicates.

**Approach:** Add a kernel event bus that dispatches only after commit, one isolated callback per subscriber, with subscribers registered at app start-up, and a kernel idempotency store that claims a per-account key inside the service transaction and stores only a result reference.

## Boundaries & Constraints

**Always:** events are frozen dataclasses defined in the emitting app's `events` module and subclass a kernel `DomainEvent` whose `name` follows `domain.past_tense` (lowercase, one dot) and is validated at class creation; payloads carry ids and plain values only; `emit` registers one `transaction.on_commit(..., robust=True)` callback per subscriber, so emitting with no subscribers registers nothing; a failing subscriber is caught, logged with the event name and handler name only (never the message, payload or traceback text), and neither the response nor other subscribers change; subscribers are registered in `AppConfig.ready` through `kernel.events.subscribe(EventType, handler)` and only from `subscribers.py` or `apps.py` modules (a convention test with a failing fixture proves it); registering the same handler twice for an event is an error; subscriber order is registration order; idempotency records are keyed by (scope, account id as a plain UUID column, key) with a unique constraint, store the request hash and a result reference (never a response body), honour a key for 24 hours, and are purged after 7 days; `claim` runs inside the caller's transaction and a rolled-back transaction leaves no record; the same key with a different request hash within 24 hours raises the existing `DomainError("idempotency_key_reused")` (422); a record older than 24 hours is treated as expired and can be reclaimed; concurrent claims of one key never create two records; import-linter contracts, `openapi.yaml` and the authorisation matrix keep passing; comments only where intent is not obvious.

**Never:** add real domain events, subscribers or endpoints; read an `Idempotency-Key` header or add presentation code (a later story wires it); store request or response bodies, emails, phones or addresses in idempotency records; add a task queue, Redis or outbox (consequences that must not be lost use AD-23, not events); build the retention runner (Story 1.9, `purge_expired` is only provided); let kernel import other apps.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Dispatch after commit | event emitted inside `atomic`, then commit | each subscriber called once, after commit, in registration order | N/A |
| Rolled back | event emitted then transaction rolls back | no subscriber is called | N/A |
| No subscribers | event with none registered | no-op, nothing scheduled | N/A |
| Autocommit | emitted outside a transaction | subscribers run immediately | N/A |
| Failing subscriber | one handler raises | failure logged (event and handler name only); other subscribers still run; request response unchanged | N/A |
| Bad event | name not `domain.past_tense`, or mutable or non-frozen definition | class creation fails | clear error |
| Duplicate subscription | same handler registered twice | error at registration | N/A |
| Subscribe outside convention | `subscribe(` called from a module other than `subscribers.py` or `apps.py` | convention test fails (failing fixture) | N/A |
| First claim | new (scope, account, key) | new claim, a record exists after commit | N/A |
| Replay | same key and same request hash within 24 hours | not-new claim carrying the original result reference | N/A |
| Reuse | same key, different request hash within 24 hours | `idempotency_key_reused` (422 envelope when raised through the API handler) | N/A |
| Other account or scope | same key, different account or scope | independent claim | N/A |
| Expired | same key after 24 hours (and before purge) | reclaimed as new | N/A |
| Purge | records older than 7 days | `purge_expired(now)` deletes them, newer ones stay | naive `now` raises ValueError |
| Concurrent | many parallel claims of one key | exactly one new claim, the rest see it (threaded test on PostgreSQL) | N/A |
| Rollback of claim | service claims then fails | no record remains | N/A |
| Invalid input | key empty, over 128 characters or with characters outside `[A-Za-z0-9._:-]`; result reference that does not look like `type:id` | ValueError | never stores free text |

</frozen-after-approval>

## Code Map

- `apps/kernel/events.py` -- `DomainEvent` base (validated name, frozen), `subscribe`, `emit`, registry with `clear` helper for tests
- `apps/kernel/idempotency.py` -- `claim(scope, account_id, key, request_hash, now=None)` returning a small `Claim` (is_new, result_ref, `complete(result_ref)`), `request_fingerprint(mapping)` (canonical JSON, sha256), `purge_expired(now)`; one-statement `INSERT ... ON CONFLICT DO NOTHING` plus a locked read for the replay and expiry paths
- `apps/kernel/models.py`, `apps/kernel/migrations/0003_idempotency_record.py` -- `IdempotencyRecord` (UUID pk, scope, account_id UUID, key, request_hash, result_ref nullable, created_at; unique on scope, account_id, key; index on created_at)
- `apps/presentation/api/exceptions.py` -- only if `idempotency_key_reused` raised as `DomainError` does not already map to 422 (it should)
- `tests/events/`, `tests/idempotency/`, `tests/architecture/` -- every matrix row, a tests-only view that emits inside `atomic` with a failing subscriber returning 200, the subscribe-location convention test with a failing fixture, role grants extended in `tests/test_db_roles.py`
- `docs/events.md`, `README.md` -- event and idempotency conventions, the `AppConfig.ready` pattern with a code sample, AD-18 versus AD-23 guidance

## Tasks & Acceptance

**Execution:**
- [x] `apps/kernel/events.py` -- event base, subscribe, emit
- [x] `apps/kernel/models.py`, migration `0003`, `apps/kernel/idempotency.py` -- store and claim
- [x] `tests/events/`, `tests/idempotency/`, `tests/architecture/`, `tests/test_db_roles.py` -- cover every matrix row
- [x] `docs/events.md`, `README.md` -- conventions

**Acceptance Criteria:**
- Given the kernel event bus, when a domain event is emitted, then it dispatches with `transaction.on_commit(robust=True)`, one callback per subscriber registered in `AppConfig.ready`, and emitting with no subscribers is a no-op.
- Given a subscriber that fails, when the event dispatches, then the failure is logged and neither the response nor other subscribers change, and no event fires for a rolled-back transaction.
- Given the idempotency store, when a service calls `claim(scope, account_id, key, request_hash)` inside its transaction, then the same key and request return the original result reference, the same key with a different request is rejected with `idempotency_key_reused`, and keys are per account, honoured 24 hours, purged after 7 days, with only a result reference stored.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | Mutable-payload check bypassed by string annotations, unions, abstract containers (BH, EC) | medium | patch | Raw `__annotations__` read; now resolved type hints walked recursively. |
| 2 | Undecorated subclass accepted and mutable; duplicate names; non-callable handler (EC, BH) | medium | patch | Frozen dataclass required at subscribe and emit; duplicates and non-callables rejected. |
| 3 | Exact-type dispatch and emit-time snapshot undocumented and unpinned (BH, EC, VG) | low | patch | Tests and docs added. |
| 4 | Subscriber-failure log not verified through the real formatter; no exception class (VG, BH) | medium | patch | Formatter-level test; `exception_class` added. |
| 5 | `Claim.complete` ignores rowcount, runs outside the transaction, can run twice (BH, EC) | medium | patch | Raises unless exactly one row updated. |
| 6 | Fingerprint collides or crashes on odd keys and non-finite floats (BH, EC) | low | patch | Rejected with ValueError; supported types documented. |
| 7 | Test named for conflict rollback never conflicts; TTL and purge boundaries untested (VG, BH, EC) | medium | patch | Rewritten with boundary tests. |
| 8 | Subscribe-location test crashes on bad files and misses aliases (EC, BH) | low | patch | Fixed with fixtures. |
| 9 | Unfinished-claim state mis-described; docs omit testing, re-entrancy, thread-safety (BH) | low | patch | Docs corrected. |
| 10 | Purge-race retry branch and extra concurrency cases untested (VG, BH) | low | defer | Narrow race; revisit with the retention runner (Story 1.9). |
| 11 | Purge deletes in one statement, no batching (EC) | low | defer | Story 1.9 owns scheduling and bounded purging. |
| 12 | Repeatable-read isolation, non-default DB alias, future created_at, raw SQL sync, extra DB constraints (EC, BH) | low | reject | Default read committed; fix adds complexity. |
| 13 | subscribe must sit inside ready(), emitters never import subscribers (BH) | low | reject | Not required by the story; location rule enforced. |

## Design Notes

Decisions made without asking: expired records (24 hours to 7 days) are reclaimed in place rather than rejected, so a retry after a day works; a claim returned as not new with no result reference means the first attempt never completed and the caller decides (documented); the key alphabet is restricted so keys cannot smuggle free text; the subscribe-location rule is enforced by an AST convention test because import-linter cannot express it; events carry no personal data by rule and the idempotency table stores a hash and a reference only; `purge_expired` is provided now and scheduled by Story 1.9.

## Verification

**Commands:**
- `uv run python manage.py migrate --database=migrator` -- applies migration 0003
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest && uv run python manage.py makemigrations --check --dry-run` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && git diff --exit-code openapi.yaml` -- expected: no diff
