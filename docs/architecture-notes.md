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

## Identity services package layout

`apps/identity/services/` is a package, split by area after Epic 1 grew the old single module to 1,379 lines. `__init__.py`
re-exports the public surface, so `from apps.identity import services` and `services.<name>` work as before; callers
(presentation, subscribers, the `provision_admin` command, other apps) never import the submodules.

| Module | Holds |
| --- | --- |
| `_common.py` | helpers used by more than one area: `_require_system`, `_require_owner_actor`, `_email_taken`, `_locked`, `_view`, `_lock_key`, `_end_open_sessions`, `_record_verified`, `_void_email_change_links`, `EMAIL_CONSTRAINT` |
| `accounts.py` | `create_account`, `_insert_account`, `suspend`, `reactivate`, `deactivate` |
| `registration.py` | `register_individual`, `register_agency`, `_register`, `_notify_existing`, `RegistrationInvalid` |
| `email_verification.py` | `mark_email_verified`, `issue_verification_token`, `confirm_email`, `request_verification_link` |
| `sessions_auth.py` | `sign_in`, `admin_sign_in`, `_attempt`, `sign_out`, `end_account_sessions`, `refresh_session`, `SignedIn` |
| `password_flows.py` | `request_password_reset`, `issue_password_reset_token`, `confirm_password_reset`, `change_password`, `PasswordInvalid` |
| `profile.py` | individual and agency profile read and update, `require_agency`, `FieldsInvalid` |
| `email_changes.py` | `request_email_change`, `issue_email_change`, `confirm_email_change`, `EmailChangeDelivery` |
| `admin.py` | `provision_admin`, `disable_admin`, `enable_admin`, `remove_admin`, `reset_admin_password`, `AdminRefused` |

Rules for the package:

- Imports run one way and never cycle: every module imports `_common`, which imports no sibling; `registration` imports
  `accounts` (the shared insert), `email_changes` imports `profile` (`FieldsInvalid`) and `admin` imports `password_flows`
  (`PasswordInvalid`). Add a shared helper to `_common` only when two areas need it.
- Names are looked up in the module that uses them, so a test that patches `make_password`, `check_password`, `emit_*` or
  `city_is_active` must patch the module that calls it (for example `sessions_auth.check_password`), not the package. The
  package re-exports only the names that code outside it reads.
- The actor-first convention test scans `apps/*/services.py` and `apps/*/services/*.py`; the admin-route tests parse every
  module of the package together. Keep modules under about 400 lines; split again by area rather than growing one.
