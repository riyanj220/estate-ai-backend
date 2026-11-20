---
title: 'Story 1.24: In-app notification list (backend half)'
type: 'feature'
created: '2026-11-20'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/docs/events.md'
  - '{project-root}/docs/audit.md'
  - '{project-root}/docs/profile.md'
  - '{project-root}/docs/sessions.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Features will need to tell account holders about outcomes that affect them, but there is no notification store, no way for a feature to raise one safely, and no way for an account holder to read or clear them.

**Approach:** Add a small shared notification service in the kernel (so every feature app can use it without importing another feature): `notify(account_id, kind, payload)` stores a notification AFTER the surrounding transaction commits (never when it rolls back), kinds are registered by the feature that produces them with a strict payload schema so no private data of another account can slip in, and signed-in account holders read their own notifications newest first with cursor pagination, read and unread state, an unread count, and mark-read actions. This spec is the backend half; the notifications page and the header unread count are a second spec after the backend merges. New Messages are not notifications (Epic 7 has its own unread indicator) and there are no email alerts.

## Boundaries & Constraints

**Always:**
- **Location and layering**: the model `Notification` and the service live in `apps/kernel` (`models.py`, new `notifications.py`), importable by every app; the kernel imports nothing from other apps. The account is referenced by a plain `UUIDField` `account_id` (no foreign key into identity; identity models are not importable from the kernel) with an index.
- **Model** `Notification`: `id` UUID pk, `seq` BigAutoField-style unique increasing integer used for ordering and cursors (a UUID is not time-ordered), `account_id` UUID, `kind` CharField 64, `payload` JSONField (validated, small), `created_at`, `read_at` nullable; index on (`account_id`, `-seq`) and on (`account_id`, `read_at`); runtime role DML through the existing hook; one migration.
- **Kinds are registered by the producing feature**: `notifications.register_kind(kind, schema)` is called from the owning app's `AppConfig.ready` (same pattern as `audit.register_action`); a kind is `app.verb` lowercase (`^[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*$`); registering twice with a different schema or an invalid name raises at start-up; `notify` with an unregistered kind raises (`ValueError`). The schema maps each allowed payload key to a type: `uuid`, `int` (bounded), `bool`, an enumerated list of allowed string values, or `short_text` (at most 120 characters) for PUBLIC-class strings only; any key not in the schema, a missing required key, a wrong type, an out-of-range int, or a `short_text` that trips `reject_contact_details` is refused (`ValueError`, no echo of the value), and the whole payload is limited to 1 KB serialised. There is no free-form payload and no nested structures.
- **`notify(account_id, kind, payload)`**: validates synchronously (so programming errors fail in the caller's tests), then schedules ONE `transaction.on_commit(robust=True)` callback that inserts the row in its own transaction; when the surrounding transaction rolls back nothing is stored; outside a transaction it stores immediately; a storage failure is logged by exception class only and never changes the caller's outcome (notifications are derived and repairable, not a must-not-lose consequence, AD-23). `account_id` must be a UUID. The function returns nothing.
- **Kinds shipped now** (registered from `identity`'s `AppConfig.ready`, raised from the existing services after commit through `notify`, empty payloads): `account.password_changed` (after a password change or reset completes) and `account.email_changed` (after an email change confirms). The old-address mail notices stay; these are the in-app counterpart. No other kinds.
- **List** `GET /api/v1/notifications`, `RequireSignedIn` (Visitors 401), own notifications only, newest first by `seq` descending using the project's cursor pagination (`StandardCursorPagination`, `cursor_ordering` a single unique field), page size default 20 and at most 50 (`page_size` query parameter, invalid values refused as validation errors or clamped as the existing pagination does), optional `state=unread|all` (default `all`). Each item `{id, kind, payload, created_at, read}` where `read` is a boolean (never the raw `read_at`). `Cache-Control: no-store`. A deactivated account has no session.
- **Unread count** `GET /api/v1/notifications/unread-count`, signed in, returns `{"unread": <int>}` (counts all unread, `no-store`).
- **Mark read** `POST /api/v1/notifications/{id}/read` (CSRF enforced by the existing authentication class, signed in): marks the caller's notification read (sets `read_at` once, idempotent, a second call keeps the first timestamp), returns `200` with the item; an id that does not exist OR belongs to another account is `404 not_found` (never `403`, so ids of others are not confirmed); a malformed id is `404`. `POST /api/v1/notifications/read-all` marks every unread notification of the caller read and returns `{"marked": <int>}`. Both `no-store`.
- **Isolation**: every query is filtered by the caller's account id in the service or selector layer (actor-first services in `kernel`: `list_for(actor, ...)`, `count_unread(actor)`, `mark_read(actor, id)`, `mark_all_read(actor)`), never trusting a request parameter for the account; Visitors get `unauthenticated`; the system actor may not read someone's notifications through these functions.
- **Retention**: job `kernel.notifications` registered through `kernel.retention.register`: read notifications older than 90 days and any notification older than 180 days are purged (dry run counts only); index support. When an account is removed (`identity.services.remove_admin` today), its notifications are deleted through a kernel function `notifications.delete_for_account` called from that service in the same transaction.
- **Privacy**: the payload schema is the privacy control: only public-class values; the canary tests prove no email, phone, address, token or password reaches a stored payload, a log, an audit record or an error through `notify`; payload values never appear in logs (class-only logging).
- **Docs**: new `docs/notifications.md` (the service contract, how a feature registers a kind and a schema, the after-commit rule, the isolation and privacy rules, routes, retention, what the frontend must do, that Messages and email alerts are out of scope), updates to `docs/events.md` (notify versus events), `docs/rate-limits.md` if relevant, `docs/profile.md`/`docs/password-reset.md` (the two kinds), `README.md`; `openapi.yaml` and `docs/authorisation-matrix.md` regenerated. All earlier tests, convention tests and import-linter contracts keep passing. Comments only where intent is not obvious.

**Never:** store a notification when the transaction rolls back; accept an unregistered kind, an unexpected key or a free-form value; put another account's data, an email, phone, address, token or password in a payload; return another account's notification or confirm its existence (404, not 403); let a request parameter choose the account; expose the raw `read_at`, `seq` or `account_id`; let a storage failure change the caller's result; send email or create notifications for new Messages; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Notify, commit | `notify` inside a transaction that commits | one row stored after commit | N/A |
| Notify, rollback | transaction rolls back | nothing stored | N/A |
| Notify outside a transaction | autocommit | stored immediately | N/A |
| Unregistered kind | unknown `kind` | `ValueError` raised at call time; nothing stored | test |
| Bad payload | extra key, missing key, wrong type, out-of-range int, over 1 KB, contact details in `short_text`, nested value | `ValueError` without echoing the value; nothing stored | test |
| Kind registration | duplicate with different schema, invalid name | start-up error | test |
| Storage failure | insert raises after commit | logged by exception class only; the caller's result unaffected | test |
| Shipped kinds | password change or reset, email change confirm | a notification of the right kind for that account only, after commit; none on failure or rollback | test |
| List | signed-in with 45 notifications | newest first, page size 20, cursor to the next page, no duplicates or gaps across pages | test |
| Page size | `page_size` 0, 51, `abc` | refused or clamped exactly as the project pagination does | test |
| State filter | `state=unread` | only unread; `state=bogus` refused | test |
| Isolation | account A lists | never sees B's notifications; same ordering and counts only for A | test |
| Unread count | mixed read and unread | exact count of unread for the caller | test |
| Mark one read | own unread id + CSRF | 200 item with `read: true`; second call idempotent, `read_at` unchanged | test |
| Mark someone else's | B's id by A | 404 `not_found`, B's notification unchanged | envelope |
| Unknown or malformed id | | 404 `not_found` | envelope |
| Mark all read | caller with unread | all of the caller's marked, others untouched; returns count | test |
| No session | any route | 401 `unauthenticated` | envelope |
| No CSRF | POST routes without a token | 403 `forbidden`; nothing changes | envelope |
| Admin and agency | any signed-in account type | own notifications only, same behaviour | test |
| Response shape | list item | exactly `{id, kind, payload, created_at, read}`; no `seq`, `account_id`, `read_at` | test |
| Privacy | logs, audit, errors, payloads | no email, phone, address, token or password (canary) | test |
| Retention | `run_retention` | read older than 90 days and any older than 180 days purged; dry run counts | test |
| Account removal | `remove_admin` | the account's notifications are deleted in the same transaction | test |
| Concurrency | parallel mark-read of the same id, parallel `notify` calls | one `read_at`, no duplicate rows lost or merged, no 500 | threaded test |
| Contract | `openapi.yaml` | four routes, item schema, freshness passes | test |

</frozen-after-approval>

## Code Map

- `apps/kernel/models.py`, `migrations/0010_notification.py` -- `Notification`
- `apps/kernel/notifications.py` -- `register_kind`, `notify`, `list_for`, `count_unread`, `mark_read`, `mark_all_read`, `delete_for_account`, purge
- `apps/kernel/apps.py` (retention registration), `apps/identity/apps.py` (register the two kinds), `apps/identity/services.py` (call `notify` after password change/reset and email change confirm; call `delete_for_account` in `remove_admin`)
- `apps/presentation/api/notifications.py` (views, serializers, pagination), `config/urls.py`
- `openapi.yaml`, `docs/authorisation-matrix.md` regenerated
- `tests/kernel/test_notifications.py`, `tests/api/test_notifications_api.py`, retention and role tests -- every matrix row
- `docs/notifications.md`, `docs/events.md`, `README.md`

## Tasks & Acceptance

**Execution:**
- [ ] model, kind registry, `notify` with the after-commit rule and payload schema validation
- [ ] list, unread count, mark read, mark all read services and endpoints with cursor pagination
- [ ] the two shipped kinds, account-removal cleanup, retention job
- [ ] tests covering every matrix row; docs and regenerated contracts

**Acceptance Criteria:**
- Given a feature calls `notify(account_id, kind, payload)`, when its transaction commits, then a notification is stored for that account after commit and not if the transaction rolls back, and the payload never contains another account's private data.
- Given I am signed in, when I request my notifications, then they are returned newest first with cursor pagination and read or unread state, and I can mark them read.
- Given later epics, when their events occur, then each registers its own notification kinds with the feature that produces it.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Design Notes

Decisions made without asking: the service lives in the kernel because every feature app must be able to call it and the Tier-4 apps may not import each other; the payload is schema-checked per registered kind (no free-form data) because that is the only reliable way to keep another account's private data out; `notify` stores after commit through the event machinery's `on_commit(robust=True)` pattern and never fails its caller because notifications are derived and repairable; a `seq` integer gives a stable newest-first cursor because UUIDs are not time-ordered; ids of other accounts are `404` so existence is never confirmed; two real kinds (password and email changes) ship now so the whole path is exercised end to end, other features register theirs later.

## Verification

**Commands (backend):**
- `uv run python manage.py migrate --database=migrator`
- `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run pytest -q && uv run python manage.py makemigrations --check --dry-run`
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- regenerate and commit
