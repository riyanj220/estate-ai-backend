# In-app notifications (Story 1.24, backend)

A small kernel service lets any feature tell an account holder about an outcome that affects them, and lets
the account holder read and clear those notifications. New Messages are not notifications (Epic 7 has its
own unread indicator) and there are no email alerts. Where this fits next to domain events: see `docs/events.md`.

## Raising a notification

Each feature registers the kinds it produces from its own `AppConfig.ready` (the same pattern as
`audit.register_action`), then calls `notify`:

```python
from apps.kernel import notifications

# AppConfig.ready
notifications.register_kind(
    "listing.approved",
    {
        "reason": ["policy", "quality"],
        "rooms": notifications.int_range(0, 50),
        "approved": bool,
        "title": notifications.SHORT_TEXT,
    },
)

# a service, inside its transaction
notifications.notify(owner_id, "listing.approved", {"reason": "quality", ...})
```

- A kind is `app.verb` in lowercase (`^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`, at most 64 characters). Registering the
  same kind twice with the same schema is a no-op; a different schema or an invalid name raises `ValueError` at
  start-up. An unregistered kind passed to `notify` raises `ValueError`.
- The schema maps every payload key (lowercase snake_case) to one type: `bool`,
  `notifications.int_range(low, high)` (bounds within +/-999,999, so an int can never hold a phone number), an
  enumerated collection of allowed strings, or `notifications.SHORT_TEXT`.
  - Enumerated values must match `^[a-z0-9_.-]{1,40}$` at registration time: no free text, emails or names.
  - `SHORT_TEXT` (1 to 120 printable characters) is for PUBLIC-class values only, such as an agency name or a city
    name. It is refused when it contains `@`, `://` or `www.`, more than 6 digits in total (separators do not help),
    or anything `reject_contact_details` flags.
  - There is deliberately no `uuid` type and no free-form or nested value. A feature that needs to carry an id
    must add the type with an explicit "public reference" justification (an id that already is public, never an
    account or another person's identifier).
- `notify` validates synchronously: an unknown or missing key, a wrong type, an out-of-range int, a refused text, a
  non-mapping payload or a payload over 1 KB serialised raises `ValueError` without echoing the value (the 1 KB size is measured in compact UTF-8). All keys are
  required. `account_id` must be a `uuid.UUID`. The function returns nothing.
- **After-commit rule.** `notify` schedules one `transaction.on_commit(robust=True)` callback that inserts the row in
  its own transaction. When the surrounding transaction (or a savepoint) rolls back, nothing is stored; outside a
  transaction the row is stored at once. A storage failure is logged by exception class and kind only and never
  changes the caller's result: notifications are derived and repairable, not a must-not-lose consequence (AD-23).
  In tests wrap the call in `django_capture_on_commit_callbacks(execute=True)`.
- **Privacy.** The schema is the privacy control: put only public-class values in a payload, never another
  account's data, an email, phone, address, token or password. Payload values never appear in logs or errors.
- Failure signal: `_store` catches every exception and logs `notification_store_failed` (error level, with the kind
  and exception class only). A spike of these events signals a grant or table problem; there is no retry job.
- Ordering uses `seq`, allocated by the column default (`nextval` of `kernel_notification_seq`) inside the INSERT.
  Two accepted properties: (a) a notification that commits late can carry a lower `seq` than one already visible,
  so clients should treat the first page plus the unread count as the source of truth and re-fetch page 1 rather
  than rely on cursor-forward polling; (b) the cursor reveals a global position counter (volume only, no content,
  and it cannot cross accounts because every query is filtered by the caller's account).
- Call `notify` as the LAST statement inside the atomic block of the flow (after the audit record, session
  ending, mail notices and event emits) so a later error cannot leave a notification for a flow that failed.

Shipped kinds (registered by `identity`, empty payloads): `account.password_changed` (a password change or a
password reset completed) and `account.email_changed` (an email change was confirmed). Both are raised in the
service after the audit record and so exist only if the change commits. The old-address mail notices stay. Later
features register their own kinds.

## Reading and clearing

Services are actor-first and always filter by the actor's own account id; no request parameter chooses the
account. A Visitor gets `unauthenticated`; the system actor is `forbidden`. `list_for(actor, state)`,
`count_unread(actor)`, `mark_read(actor, id)`, `mark_all_read(actor)`.

| Route | Notes |
| --- | --- |
| `GET /api/v1/notifications` | Signed in (any account type). Newest first by `seq`, cursor pagination (`next`, `previous`, `results`), 20 per page, `page_size` up to 50 (a larger value is clamped, `0` or a non-number falls back to 20), `state=unread\|all` (default `all`; anything else is `400 validation_error`; a garbage or tampered `cursor` is `400 validation_error` with `fields.cursor`). |
| `GET /api/v1/notifications/unread-count` | `{"unread": n}`, counting every unread notification. |
| `POST /api/v1/notifications/{id}/read` | CSRF required. Sets `read_at` once (a repeat keeps the first time) and returns the item. Another account's, an unknown and a malformed id are all `404 not_found` (never `403`). |
| `POST /api/v1/notifications/read-all` | CSRF required. Marks all of the caller's unread read; returns `{"marked": n}`. |

An item is exactly `{id, kind, payload, created_at, read}`: `read` is a boolean; `seq`, `account_id` and the raw
`read_at` are never returned. Every answer, errors included, is `Cache-Control: no-store`. A cursor is opaque but
encodes the last `seq` position.

## Kinds raised by the listings app

`listing.draft_expiring` (Story 2.13): payload `{listing_id: UUID_TEXT, days_left: int 0..365}`, raised after commit by the
`listings.draft_notice` retention job to the owner only. `notifications.UUID_TEXT` is a schema type for an id: a
`uuid.UUID` or its canonical lowercase text, stored as text (a `SHORT_TEXT` cannot carry an id: its digit limit exists to
keep phone numbers out). A due Draft whose notification was never stored, or whose deletion date passed long ago, is noticed again (30 or 7
days left) before anything is deleted (`docs/listings.md`). The payload never holds a title, address or other content; the frontend renders a fixed sentence
from the kind and links to the Draft.

## Retention and removal

The `kernel.notifications` retention job deletes notifications read more than 90 days ago (`read_at`) and any
notification created more than 180 days ago (`created_at`), in batches of 1000 ids per statement (a dry run only
counts). When an account is removed (`identity.services.remove_admin` today) its notifications are deleted by
`notifications.delete_for_account` in the same transaction, and `notifications.delete_for_account_after_commit` is
registered as the last step so a notification queued earlier in that transaction (stored by its own after-commit
callback) is removed too. Every other account-removal flow must do both.

## What the frontend must do

The routes are not rate limited (signed in, cheap, own rows only).

Fetch the unread count for the header and the list for the notifications page; never cache either; render text
from the `kind` and `payload` (the backend sends no display text); call the read routes with the CSRF token; treat
`404` on mark-read as "already gone". Messages and email alerts are out of scope.
