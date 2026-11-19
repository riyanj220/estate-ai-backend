# Architecture notes

## AD-14 spike: synchronous views under Daphne

Date: 2026-11-19. Script: `scripts/asgi_thread_spike.py` (run with `uv run python scripts/asgi_thread_spike.py`).
Stack: Django 5.2, Daphne (one process, one worker), Python 3.12.

Method: a throwaway Django project (minimal settings, no database, no middleware) serves a sync view (`time.sleep(1)`) and an async view
(`asyncio.sleep(1)`). Four concurrent requests (one measurement run) are sent to each, then a mix of two sync and two async.

| Burst | Total wall time |
| --- | --- |
| 4 concurrent sync requests | 1.02 s |
| 4 concurrent async requests | 1.01 s |
| 2 sync + 2 async | 1.03 s |

Observation: synchronous views did **not** share a single thread. Four one-second sync requests finished
in about one second, so they ran concurrently (a plausible explanation, not verified here: Django wraps each ASGI request in its own
thread-sensitive context).

**Result: the AD-14 assumption ("under ASGI synchronous views share one thread") is false on this stack.
AD-14 is flagged for amendment.** Consequences to carry into the amendment:

- A slow sync view does not block other requests, so the "async or DB-only" restriction on message send,
  sign-in and session endpoints is not needed to protect chat latency.
- Sync views may still run on a bounded thread pool, so unbounded blocking work could exhaust it; pool size
  and exhaustion behaviour were **not measured**. The bounded executor in `kernel.runtime` for AI calls, image re-encode and inference remains justified.
- The single-process, single-worker rule is unaffected (it exists because of the in-memory channel layer).
- Concurrency means sync code may now run in parallel threads, so shared in-process state needs thread safety.
