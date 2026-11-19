# Runtime executor, feature flags, AI request log and retention (Story 1.9)

All four live in `apps/kernel` and import no other app.

## Runtime executor (`kernel.runtime`, AD-14)

One thread pool of `RUNTIME_EXECUTOR_MAX_WORKERS` threads (default 4). Heavy work may use at most total minus
`RUNTIME_EXECUTOR_RESERVED_WORKERS` (default 1) of them, so chat and sign-in always have a free worker. Start-up
fails with `ImproperlyConfigured` naming the setting if reserved is below 1 or not below the total.

```python
from apps.kernel import runtime

future = runtime.submit_heavy(resize_image, path)      # returns at once; ExecutorBusy if heavy slots are full
future = runtime.submit_interactive(short_task)        # not limited by the heavy slots
result = await runtime.run_heavy(call_model, prompt)   # async views: awaits without holding a thread
```

- Heavy submission never blocks and never queues. On `ExecutorBusy` the caller falls back (FR-38): tell the user
  the feature is busy, or use the non-AI path.
- Tasks run with a copy of the caller's context variables, so the request id is visible in the worker.
- A task that raises leaves the exception on its future; the log records only the exception class.
- Limits are per process: N server processes mean N pools (the architecture runs one worker). Heavy work can
  never occupy more than total minus reserved workers, and queued heavy tasks are bounded by the same slots
  (a slot is held from submission until the task finishes or is cancelled). Interactive work is not separately
  bounded. A worker closes its database connections after each task.
- Under ASGI a synchronous view holds the one request thread, so a view that waits on heavy work must be `async`
  and use `run_heavy`. Tests call `runtime.shutdown()` to reset the executor.

## Feature flags (`kernel.flags`)

Flags are declared in code with a default and overridden in the database. Reading or setting an undeclared name
raises `UndeclaredFlag`, so typos fail loudly.

```python
# in an app's AppConfig.ready
from apps.kernel import flags
flags.register_flag("assistant_voice", False, "Voice input in the Listing Assistant.")

flags.is_enabled("assistant_voice")
flags.set_flag(actor, "assistant_voice", True)   # admin or system only
```

`set_flag` takes the actor first: a visitor gets `unauthenticated`, a signed-in non-admin `not_found`. The row records
the state, the account id (null for the system) and the time. Flag changes are not yet audited.
If the database cannot be read, `is_enabled` logs the exception class and returns the declared default.
The kernel declares `ai_gateway`, default on, which the AI gateway will read.

## AI request log (`kernel.ai_log`)

`record_ai_call(feature, provider, model, latency_ms, outcome)` stores exactly those fields plus the time. Feature,
provider and model are short lowercase tokens (`[a-z0-9][a-z0-9_.-]{0,47}`); pass outside ids through
`normalise_token` first (lowercase, invalid characters become `-`, cut to 48); outcome is one of `success`, `error`,
`timeout`, `fallback`, `budget_exhausted`, `blocked`; latency is a non-negative integer. Anything else raises
`ValueError` and stores nothing; latency above 2147483647 and a naive `now` are also refused. The log has no
account id, prompt, response or free text by design, and constrains the shape of its fields; it cannot prove a
well-formed token carries no content. `record_ai_call` can raise (invalid input, database error), so callers must
guard it: logging must never fail the user-facing AI call.

## Retention (`kernel.retention`, AD-23)

`register(name, purge)` where `purge(now, dry_run)` returns the number of rows deleted, or in dry-run the number that
would be deleted, deleting nothing. An app registers its job from `AppConfig.ready` when it adds data with a retention
rule:

```python
from apps.kernel import retention
retention.register("listings.stale_drafts", purge_stale_drafts)   # name: lowercase, dots allowed, unique
```

```bash
uv run python manage.py run_retention --dry-run   # one line per job, nothing deleted
uv run python manage.py run_retention
```

Jobs run in registration order, each in its own transaction. A failing job is logged (name and exception class only),
the rest still run, and the command exits non-zero at the end. The kernel registers expired idempotency records
(older than 7 days), ended rate-limit counters, and AI request log rows older than 90 days (PRD section 8.3).

A job must return an int row count; anything else counts as a failed job. Runs must not overlap: schedule a single
instance (there is no advisory lock yet). Purges delete in one statement for now, with no batching.

The schedule (cron or a platform scheduler) is decided at deployment; nothing here schedules the command.
