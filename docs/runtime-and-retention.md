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
future = runtime.submit_background(recompute, key)     # nobody waits: one worker of its own, bounded queue
```

- Work that no request waits for (the price estimate after publication or an edit) uses `submit_background`: one
  separate thread with up to `BACKGROUND_QUEUE_LIMIT` (100) tasks queued, so it never takes a heavy slot. Before
  this, estimates started by a few publications could make the next image upload answer `busy`.

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

Story 2.9 adds `kernel.media_orphans` (`kernel.media.purge_orphans`): unattached media assets and stray stored files older
than 24 hours, at most 500 of each per run, files deleted after commit; see `docs/media.md`. The image pipeline is the
first user of `submit_heavy` for real work (`kernel.images.process_image`, 20 second timeout, `Busy` when the heavy
slots are full). Caveats: the upload view is synchronous and blocks on the future for up to 20 s, occupying the ASGI
thread-sensitive thread (a known limitation, to move to an async view later); a timed-out job keeps its slot until it
ends; image decodes are further limited to two at a time; the orphan job walks the whole store each run (fine at MVP
scale); and a reverse proxy should cap request bodies (the upload route's 6 MiB cap is enforced by the application too,
see `docs/media.md`).

Story 2.13 adds `listings.draft_notice` and `listings.draft_expiry` (see `docs/listings.md`): a notice after 180 days
without activity and deletion 30 days later unless the Draft was opened or edited. They process at most 200 Drafts a run
and isolate each Listing in its own savepoint.

Story 8.2 adds `listings.view_buckets`: the 30-minute view-counting buckets, deleted once expired.

Story 4.3 adds `valuation.estimates` (see `docs/valuation.md`): estimates of deleted Listings, and estimates of
entered attributes older than 90 days.

Story 9.5 adds `governance.report_snapshots`: anonymises Conversation Report snapshots of Deactivated participants
that the deactivation subscriber missed.

`run_flags` (Story 9.6) is separate from retention: schedule it nightly too. It refreshes the advisory duplicate and
suspicious Listing flags.

A job must return an int row count; anything else counts as a failed job. Runs must not overlap: schedule a single
instance (there is no advisory lock yet). Purges delete in one statement for now, with no batching.

The schedule (cron or a platform scheduler) is decided at deployment; nothing here schedules the command.

Story 5.8 adds `assistant.history`: Assistant sessions and turns of deleted Listings (also removed at once on
`listing.deleted`).

### Stories 10.11 and 10.12: periods and jobs

| Data (PRD 8.3) | Kept | Job |
|---|---|---|
| Idempotency records | 7 days | `kernel.idempotency` |
| Rate-limit counters | until the window ends | `kernel.ratelimit` |
| AI request log | 90 days | `kernel.ai_request_log` |
| Notifications | 90 days after being read (180 days at most) | `kernel.notifications` |
| Publication events (with fingerprint) | 24 months | `listings.publication_events` * |
| View-counting buckets | until expired | `listings.view_buckets` |
| Assistant history | 90 days after first Publication | `assistant.published_history` |
| Reports, evidence, review history | 24 months after resolution; open ones kept | `governance.reports` * |
| Verification requests and evidence | 24 months after the decision; an approval in force keeps its row, its registration number and note are emptied | `governance.verification` * |
| Moderation records | 24 months | `governance.moderation_records` * |
| Support requests | 12 months after closure; open ones kept | `governance.support_requests` |
| Decided account closure requests | 24 months after the decision | `governance.closure_requests` |
| Audit records | 24 months | not a job: `purge_audit_records` (`docs/audit.md`) * |

\* deleted as the `purger` database role (`docs/audit.md`), through `retention.delete_with_purge_role`; set
`PURGE_PASSWORD` where retention is scheduled. Months are calendar months (`retention.months_ago`).

**Application logs (30 days).** The application stores no logs: it writes structured JSON to stdout only
(`LOGGING` in `config/settings/base.py`), so there is nothing for `run_retention` to purge. Whatever captures stdout
at deployment (journald, Docker's log driver, a hosting platform) must be configured to keep at most 30 days, for
example `MaxRetentionSec=30day` for journald.
