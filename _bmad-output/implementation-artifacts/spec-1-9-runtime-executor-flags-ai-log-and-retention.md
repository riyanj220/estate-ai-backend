---
title: 'Story 1.9: Runtime executor, feature flags, AI request log and retention hooks'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'e53c7630f8a47b07c80c2933a826a5da925b4ede'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-8-domain-events-and-idempotency.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AI calls, image work and inference must not starve chat and sign-in, features need a kill switch, AI calls need a content-free log, and data kept for limited periods needs one place that purges it, none of which exists yet.

**Approach:** Add four small kernel primitives: a bounded runtime executor with capacity reserved for interactive work, a feature-flag store, a content-free AI request log, and a retention hook protocol with a `run_retention [--dry-run]` command that also purges the kernel's own expired data.

## Boundaries & Constraints

**Always:** all four live in `apps/kernel` (kernel imports no other app); the executor has a configurable total worker count and a configurable reserved count (`RUNTIME_EXECUTOR_MAX_WORKERS` default 4, `RUNTIME_EXECUTOR_RESERVED_WORKERS` default 1, reserved must be at least 1 and less than the total, validated at start-up) so heavy work can never use more than total minus reserved workers; heavy submission never blocks the caller: it returns a future at once, or raises `ExecutorBusy` immediately when heavy capacity is full; worker tasks run with the caller's context variables (request id) copied; an awaitable `run_heavy` lets async views wait without holding a thread; feature flags are declared in code with a default (`register_flag(name, default, description)`) and overridden in the database; reading an undeclared flag raises, so typos fail loudly; `set_flag(actor, name, enabled)` takes the actor first, allows only administrator or system actors (visitors get `unauthenticated`, other users `not_found`) and records the account id (plain UUID, null for system) and time; the kernel declares one flag, `ai_gateway`, default on; the AI request log stores exactly feature, provider, model, latency in milliseconds, outcome and created time, never content, account ids, prompts, responses or free text, with feature, provider and model restricted to short lowercase tokens and outcome to a fixed set (`success`, `error`, `timeout`, `fallback`, `budget_exhausted`, `blocked`); retention jobs are registered with `kernel.retention.register(name, purge)` where `purge(now, dry_run)` returns the number of rows deleted or, in dry-run, that would be deleted and deletes nothing; `run_retention` runs every registered job in registration order, each in its own transaction, isolates a failing job (log its name and exception class, continue, exit non-zero at the end), supports `--dry-run`, and prints one line per job; the kernel registers its own jobs for expired idempotency records (7 days), ended rate-limit counters, and AI request log rows older than 90 days (PRD section 8.3); import-linter contracts, `openapi.yaml`, the authorisation matrix and the subscribe-location test keep passing; comments only where intent is not obvious.

**Never:** call a real AI provider or build the AI gateway or redaction (later epic), build the AI budget ledger, add a task queue, Redis or scheduler (the schedule is decided at deployment), store request or response content in the log, build the audit trail (Story 1.10), or add endpoints.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Heavy work | `submit_heavy(fn)` | returns a future in well under 200 ms regardless of how long `fn` runs; runs on a worker | N/A |
| Async wait | async view awaits `run_heavy(fn)` for a slow task | other requests are served meanwhile (concurrent fast request completes within 200 ms) | N/A |
| Heavy full | all heavy slots busy | `ExecutorBusy` raised immediately | caller falls back (documented) |
| Reserved capacity | heavy slots saturated | an interactive task still starts promptly on a reserved worker | N/A |
| Context | task reads the request id context variable | sees the caller's value | N/A |
| Task failure | task raises | future carries the exception; log has the exception class only | N/A |
| Bad config | reserved is 0 or not below total | start-up error naming the setting | `ImproperlyConfigured` |
| Flag default | declared flag with no row | default returned | N/A |
| Flag change | admin or system sets a flag | state, who and when recorded; reads see it | N/A |
| Flag refused | visitor or user sets a flag | `unauthenticated` or `not_found`, nothing changes | N/A |
| Undeclared flag | read or set unknown name | error raised | N/A |
| AI log | valid record | one row with the six fields | N/A |
| AI log invalid | content-like feature, uppercase, over-long, unknown outcome, negative latency | ValueError, nothing stored | N/A |
| Retention registration | register a job | listed in order; duplicate name rejected | N/A |
| Dry run | `run_retention --dry-run` | each job reports its count, nothing deleted | N/A |
| Real run | `run_retention` | expired idempotency, counter and AI log rows deleted, newer rows kept | N/A |
| Failing job | one job raises | other jobs still run, error reported, exit code non-zero | N/A |
| Kernel isolation | import-linter | kernel imports no app | contract passes |
| Runtime role | `runtime` role | can read and write flags, AI log and run purges; cannot run DDL | role test |

</frozen-after-approval>

## Code Map

- `apps/kernel/runtime.py` -- lazily created singleton executor, `submit_heavy`, `submit_interactive`, `run_heavy` (awaitable), `ExecutorBusy`, settings validation, context-variable propagation, shutdown hook for tests
- `apps/kernel/flags.py` -- `register_flag`, `is_enabled`, `set_flag`; `apps/kernel/ai_log.py` -- `record_ai_call`; `apps/kernel/retention.py` -- `register`, `run_all(now, dry_run)`, registry
- `apps/kernel/models.py`, `apps/kernel/migrations/0004_flags_and_ai_request_log.py` -- `FeatureFlag` (uuid pk, name unique, enabled, changed_by account id nullable, changed_at) and `AiRequestLog` (uuid pk, feature, provider, model, latency_ms, outcome, created_at with index)
- `apps/kernel/apps.py` -- `ready()` registers the kernel flag and the three kernel retention jobs
- `apps/kernel/ratelimit.py`, `apps/kernel/idempotency.py` -- give `purge_expired` a `dry_run` option (count only) without changing current behaviour
- `apps/kernel/management/commands/run_retention.py` -- the command
- `config/settings/base.py` -- executor settings with validation
- `tests/runtime/`, `tests/flags/`, `tests/ai_log/`, `tests/retention/`, `tests/test_db_roles.py` -- every matrix row including the 200 ms async test
- `docs/runtime-and-retention.md`, `README.md` -- how to use each primitive, how a later app registers a flag or a retention job, the deployment schedule note

## Tasks & Acceptance

**Execution:**
- [x] `apps/kernel/runtime.py`, settings -- bounded executor with reserved capacity
- [x] models, migration `0004`, `flags.py`, `ai_log.py` -- flags and AI request log
- [x] `retention.py`, command, kernel jobs, `dry_run` on existing purges -- retention hooks
- [x] tests and role grants -- cover every matrix row
- [x] docs and README

**Acceptance Criteria:**
- Given `kernel.runtime`, when AI, image or inference work runs, then it executes on a bounded executor with a configurable concurrency limit and capacity reserved for chat and sign-in, and a test shows heavy work does not hold the request thread for more than 200 ms of non-database time.
- Given the feature-flag store, when a flag is read or changed, then it records name, state, who changed it and when, and is readable by kernel code.
- Given the AI request log, when a feature records a call, then it stores feature, provider, model, latency and outcome with no content.
- Given the retention hook protocol, when an app registers a purge job, then `run_retention [--dry-run]` runs every registered job without the kernel importing any app, and dry-run reports what each job would delete.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | Cancelled queued heavy future never releases its slot (BH, EC) | high | patch | Repeated client disconnects would leave permanent `ExecutorBusy`; release moved to a once-only done-callback, tests added. |
| 2 | Slot release when pool submit raises untested (VG) | medium | patch | Test added. |
| 3 | Worker threads leak database connections (EC) | medium | patch | close_old_connections after each task. |
| 4 | Real model ids rejected by the AI log; latency overflow; naive now; "no content" overstated (BH, EC) | medium | patch | `normalise_token`, bounds and docs added; strict validation kept per spec. Token regex now also allows a leading digit. |
| 5 | Flag read error breaks every caller; non-string name; naive now (EC) | medium | patch | Fail to declared default, logged by class only. |
| 6 | Purge result unchecked; double ready() crashes; empty job list silent (EC) | medium | patch | Fixed with tests. |
| 7 | Meaningless assertion; CommandError message and exit untested; startup validation untested (VG, BH) | low | patch | Fixed. |
| 8 | Limits per process, reserved-capacity wording, overlap and batching undocumented (BH, EC) | low | patch | Documented precisely. |
| 9 | Unbounded purge, no overlap guard, no job filter or summary line (BH, EC) | low | defer | Deployment-time concerns; recorded with the retention note. |
| 10 | Interactive tasks unbounded; FIFO weakens reserved guarantee (EC) | low | reject | Heavy work is bounded to total minus reserved; stated in docs. |
| 11 | Settings imports a kernel module; no flag caching or listing; changed_at default; no log on set_flag (BH) | low | reject | Fine for now; fix adds complexity. |
| 12 | Wall-clock thresholds may flake (VG, BH) | low | reject | The 200 ms bound is the acceptance criterion; stable over repeated runs. |
| 13 | Runtime role can modify AI log rows; ratelimit purge as runtime untested; fork safety (BH, EC) | low | reject | Blanket DML is by design; one worker per spine. |

## Design Notes

Decisions made without asking: a single thread pool with a heavy-slot semaphore gives reserved capacity without two pools; heavy overload rejects instead of queueing so callers fall back (FR-38) rather than wait; flags are declared in code (typo-safe) and only overridden in the database, with no audit history until Story 1.10; the AI log keeps no account id to stay content- and identity-free; `purge(now, dry_run)` is the whole retention protocol, and each app registers its own job from `apps.py` when it adds data with a retention rule; the schedule (cron or platform scheduler) is decided at deployment; the 200 ms requirement is demonstrated with an async view because under ASGI the request thread must be free while heavy work runs.

## Verification

**Commands:**
- `uv run python manage.py migrate --database=migrator` -- applies migration 0004
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest && uv run python manage.py makemigrations --check --dry-run` -- expected: all pass
- `uv run python manage.py run_retention --dry-run` -- expected: one line per registered job
- `uv run python manage.py spectacular --validate --file openapi.yaml && git diff --exit-code openapi.yaml` -- expected: no diff
