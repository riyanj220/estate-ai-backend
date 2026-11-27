# Domain events and idempotency

## Events (AD-18)

An event is a frozen dataclass in the emitting app's `events` module, subclassing `apps.kernel.events.DomainEvent`.
Its `name` is `domain.past_tense` (lowercase, one dot) and is validated when the class is created; a
non-frozen `@dataclass` or a `list`/`dict`/`set` field fails at import. Payloads hold ids and plain values only,
never emails, phones, addresses or message text.

```python
# apps/listings/events.py
@dataclass(frozen=True)
class ListingPublished(DomainEvent):
    name = "listing.published"
    listing_id: UUID
    account_id: UUID
    republished: bool = False  # True for an Archived Listing republished (Story 2.18)
```

`kernel.events.emit(event)` registers one `transaction.on_commit(..., robust=True)` callback per subscriber.
Rolled-back work never reaches a subscriber; with no subscribers nothing is scheduled; outside a transaction
subscribers run immediately. A subscriber that raises is logged (`estate.events`, event and handler name only)
and neither the response nor the other subscribers change. Subscribers run in registration order. The same
handler registered twice for an event is a `ValueError`.

Subscribers register in `AppConfig.ready`, and `kernel.events.subscribe(...)` may be called only from a
`subscribers.py` or `apps.py` module (`tests/architecture/test_subscribe_location.py`). Emitters never import
subscribers.

```python
# apps/valuation/apps.py
class ValuationConfig(AppConfig):
    name = "apps.valuation"

    def ready(self):
        from apps.kernel import events
        from apps.listings.events import ListingPublished
        from apps.valuation import subscribers

        events.subscribe(ListingPublished, subscribers.on_listing_published)
```

Matching is on the exact event type: a subscriber of a base event does not receive a subclass event. The
subscriber list is snapshotted when `emit` is called, so a handler registered after `emit` but before commit is
not called for that event.

### Testing a subscriber

Call the handler directly, or emit inside `django_capture_on_commit_callbacks(execute=True)`. To isolate the
registry, use `monkeypatch.setattr(events, "_registry", {})` in an autouse fixture (see `tests/events/test_events.py`),
or `events.clear()` when no real subscribers exist.

### Re-entrancy and threads

A subscriber that emits registers new on-commit callbacks; outside a transaction they run immediately, inside one
they wait for that commit. The registry is a module-level dict written only at start-up (`AppConfig.ready`); it is
not thread-safe for registration after start-up.

### Events or notifications?

An event tells other parts of the system that something happened; `kernel.notifications.notify` tells an account
holder. `notify` follows the same after-commit rule (`on_commit(robust=True)`, nothing stored on rollback, a failure
never reaches the caller) but is a direct call with a registered schema, not a subscription. See `docs/notifications.md`.

### Events or AD-23?

Use an event only for a derived, repairable consequence (an estimate, a notification, a socket close): if it is
lost, something recomputes or the user can retry. A consequence that must not be lost (deactivation, suspension,
erasure, a counted quota) is done by orchestration in the originating transaction (AD-23), not by an event.
Audit records are written in the same transaction as the action. No external call occurs inside a transaction.

`listing.published` and `listing.model_inputs_changed` are subscribed by `valuation`, which computes the estimate on
the runtime executor (`docs/valuation.md`).

`listing.deleted` (a Draft or sample Listing was removed) is subscribed by `valuation`, which deletes its stored
estimates; its retention job repairs a lost event.

`messaging.message_sent` (ids and the sequence number) has no subscriber yet; live delivery (Story 7.3) adds it.

## Idempotency (AD-17)

A service calls `kernel.idempotency.claim(scope, account_id, key, request_hash)` inside its transaction.
`request_hash = request_fingerprint(mapping)` (SHA-256 of canonical JSON). The result is a `Claim`:

- `is_new=True`: do the work, then `claim.complete("type:id")` with a reference to what was created.
- `is_new=False` with `result_ref`: a replay; return the referenced result.
- `is_new=False` without `result_ref`: should not occur (claim and `complete` share one transaction); it means a service bug or a missed `complete()`. Treat it as a conflict (409-style, retry later), not as a reason to redo the work.
- Same key, different hash within 24 hours: `DomainError("idempotency_key_reused")` (422).

Records are per (scope, account, key). A key is honoured for 24 hours; an older record is reclaimed as new;
`purge_expired(now)` deletes records older than 7 days (scheduled by Story 1.9). Only the request hash and the
reference are stored, never a body. A rolled-back transaction leaves no record. Keys are 1-128 characters from
`[A-Za-z0-9._:-]`; `Idempotency-Key` header handling and scope naming will be wired by the first endpoint that needs it.
`request_fingerprint` accepts str, int, finite float, bool, None, list, dict with string keys, UUID and aware datetime.
