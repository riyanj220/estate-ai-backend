"""In-app notifications (Story 1.24): a kernel service any feature app can call.

A feature registers each kind it raises from `AppConfig.ready` with a strict payload schema
(`register_kind`, like `audit.register_action`) and calls `notify(account_id, kind, payload)`.
`notify` validates at once, then stores the row after the surrounding transaction commits, so
rolled-back work never notifies and a storage failure never changes the caller's outcome
(notifications are derived and repairable, AD-23). Reading is actor-first and always filtered
by the actor's own account id. See `docs/notifications.md`.
"""

import json
import logging
import re
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.kernel.actor import Actor, require_signed_in
from apps.kernel.errors import DomainError
from apps.kernel.models import Notification
from apps.kernel.privacy.patterns import LONG_DIGIT_RUN, reject_contact_details

logger = logging.getLogger("estate.notifications")

_KIND = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
KIND_MAX = 64
SHORT_TEXT_MAX = 120
PAYLOAD_MAX_BYTES = 1024
INT_LIMIT = 999_999  # fewer than 7 digits, so an int can never carry a phone number
READ_RETENTION = timedelta(days=90)
ANY_RETENTION = timedelta(days=180)
STATES = ("all", "unread")
PURGE_BATCH = 1000
MAX_DIGITS = 6  # a short_text holding more digits in total is refused, whatever the separators
_ENUM_VALUE = re.compile(r"^[a-z0-9_.-]{1,40}$")
_URL_HINTS = ("@", "://", "www.")

SHORT_TEXT = "short_text"  # schema type: at most 120 characters of PUBLIC-class text


@dataclass(frozen=True)
class IntRange:
    low: int
    high: int


def int_range(low: int, high: int) -> IntRange:
    """Schema type: an int from `low` to `high` inclusive (each within +/-999,999)."""
    for bound in (low, high):
        if isinstance(bound, bool) or not isinstance(bound, int) or abs(bound) > INT_LIMIT:
            raise ValueError("int bounds must be ints within +/-999999")
    if low > high:
        raise ValueError("int bounds are reversed")
    return IntRange(low, high)


_kinds: dict[str, dict[str, Any]] = {}


def _normalise(schema: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, Mapping):
        raise ValueError("schema must be a mapping")
    out: dict[str, Any] = {}
    for key, kind in schema.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError("payload keys must be lowercase snake_case")
        if kind is bool or kind == SHORT_TEXT or isinstance(kind, IntRange):
            out[key] = kind
        elif isinstance(kind, Collection) and not isinstance(kind, str | bytes) and kind:
            try:
                values = frozenset(kind)
            except TypeError:
                raise ValueError("enumeration values must be strings") from None
            if not all(isinstance(v, str) and _ENUM_VALUE.fullmatch(v) for v in values):
                raise ValueError("enumeration values must match [a-z0-9_.-], 1 to 40 characters")
            if any(LONG_DIGIT_RUN.search(v) for v in values):
                raise ValueError("enumeration values must not hold long digit runs")
            out[key] = values
        else:
            raise ValueError("a payload type must be bool, int_range, SHORT_TEXT or an enumeration")
    return out


def register_kind(kind: str, schema: Mapping[str, Any]) -> None:
    """Declare a kind and the exact payload it carries. Call from `AppConfig.ready`."""
    if not isinstance(kind, str) or not _KIND.fullmatch(kind) or len(kind) > KIND_MAX:
        raise ValueError("kind must look like domain.past_tense")
    definition = _normalise(schema)
    if _kinds.setdefault(kind, definition) != definition:
        raise ValueError(f"kind {kind!r} is already registered with a different schema")


def registered_kinds() -> dict[str, dict[str, Any]]:
    return dict(_kinds)


def _check_value(key: str, spec: Any, value: Any) -> Any:
    # Messages name the schema key, never the submitted value.
    if isinstance(spec, frozenset):
        if not isinstance(value, str) or value not in spec:
            raise ValueError(f"payload {key!r} is not an allowed value")
        return value
    if spec is bool:
        if not isinstance(value, bool):
            raise ValueError(f"payload {key!r} must be a bool")
        return value
    if isinstance(spec, IntRange):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"payload {key!r} must be an int")
        if not spec.low <= value <= spec.high:
            raise ValueError(f"payload {key!r} is out of range")
        return value
    if spec == SHORT_TEXT:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= SHORT_TEXT_MAX
            or not value.isprintable()
        ):
            raise ValueError(f"payload {key!r} must be 1 to {SHORT_TEXT_MAX} printable characters")
        lowered = value.lower()
        if (
            any(hint in lowered for hint in _URL_HINTS)
            or sum(c.isdigit() for c in value) > MAX_DIGITS
            or reject_contact_details(value) is not None
        ):
            raise ValueError(f"payload {key!r} looks like contact details")
        return value
    raise ValueError(f"payload {key!r} has an unsupported type")


def _validate(kind: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    schema = _kinds.get(kind) if isinstance(kind, str) else None
    if schema is None:
        raise ValueError("unregistered notification kind")
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    if set(payload) != set(schema):
        raise ValueError("payload keys do not match the registered schema")
    clean = {key: _check_value(key, spec, payload[key]) for key, spec in schema.items()}
    if (
        len(json.dumps(clean, separators=(",", ":"), ensure_ascii=False).encode())
        > PAYLOAD_MAX_BYTES
    ):
        raise ValueError("payload is too large")
    return clean


def notify(account_id: uuid.UUID, kind: str, payload: Mapping[str, Any] | None = None) -> None:
    """Store a notification for `account_id` after the surrounding transaction commits.

    Validates now (a programming error fails in the caller's tests). Nothing is stored when the
    transaction rolls back; outside a transaction the row is stored at once. A storage failure
    is logged by exception class only and never reaches the caller.
    """
    if not isinstance(account_id, uuid.UUID):
        raise ValueError("account_id must be a UUID")
    clean = _validate(kind, payload)
    transaction.on_commit(partial(_store, account_id, kind, clean), robust=True)


def _store(account_id: uuid.UUID, kind: str, payload: dict[str, Any]) -> None:
    try:
        # `seq` comes from the column default (a database sequence) inside the insert itself.
        Notification.objects.create(
            account_id=account_id, kind=kind, payload=payload, created_at=timezone.now()
        )
    except Exception as exc:
        # Only the class and kind: the message and payload may carry private values. A spike of
        # `notification_store_failed` means a grant or table problem (docs/notifications.md).
        logger.error(
            "notification_store_failed",
            extra={
                "event": "notification_store_failed",
                "notification_kind": kind,
                "exception_class": type(exc).__name__,
            },
        )


# --- reading (actor-first; every query is filtered by the actor's own account) -------------------


def _owner(actor: Actor) -> uuid.UUID:
    """Signed in with an account of its own; the system actor reads nobody's notifications."""
    require_signed_in(actor)
    if actor.account_id is None:
        raise DomainError("forbidden")
    return actor.account_id


def _parse_id(raw) -> uuid.UUID | None:
    """Only the canonical lowercase hyphenated form is an id."""
    if isinstance(raw, uuid.UUID):
        return raw
    if isinstance(raw, str) and len(raw) == 36:
        try:
            value = uuid.UUID(raw)
        except ValueError:
            return None
        return value if str(value) == raw else None
    return None


def list_for(actor: Actor, state: str = "all") -> QuerySet[Notification]:
    """The actor's notifications, newest first. `state` is `all` or `unread`."""
    account_id = _owner(actor)
    if state not in STATES:
        raise DomainError("validation_error")
    rows = Notification.objects.filter(account_id=account_id)
    if state == "unread":
        rows = rows.filter(read_at__isnull=True)
    return rows.order_by("-seq")


def count_unread(actor: Actor) -> int:
    return Notification.objects.filter(account_id=_owner(actor), read_at__isnull=True).count()


def mark_read(actor: Actor, notification_id) -> Notification:
    """Mark one of the actor's notifications read (once; a repeat keeps the first time).

    Someone else's, an unknown and a malformed id are all `not_found`.
    """
    account_id = _owner(actor)
    target = _parse_id(notification_id)
    if target is None:
        raise DomainError("not_found")
    scoped = Notification.objects.filter(id=target, account_id=account_id)
    scoped.filter(read_at__isnull=True).update(read_at=timezone.now())
    row = scoped.first()
    if row is None:
        raise DomainError("not_found")
    return row


def mark_all_read(actor: Actor) -> int:
    """Mark every unread notification of the actor read; returns how many changed."""
    return Notification.objects.filter(account_id=_owner(actor), read_at__isnull=True).update(
        read_at=timezone.now()
    )


# --- cleanup --------------------------------------------------------------------------------------


def delete_for_account(account_id: uuid.UUID) -> int:
    """Delete every notification of a removed account, in the caller's transaction."""
    if not isinstance(account_id, uuid.UUID):
        raise ValueError("account_id must be a UUID")
    return Notification.objects.filter(account_id=account_id).delete()[0]


def delete_for_account_after_commit(account_id: uuid.UUID) -> None:
    """Delete the account's notifications once the surrounding transaction commits.

    A notification queued earlier in the same transaction is stored by its own after-commit
    callback, so an account-removal flow calls this as its LAST step (callbacks run in order) to
    remove that row too. Failure is logged by class only.
    """
    if not isinstance(account_id, uuid.UUID):
        raise ValueError("account_id must be a UUID")
    transaction.on_commit(partial(_delete_after_commit, account_id), robust=True)


def _delete_after_commit(account_id: uuid.UUID) -> None:
    try:
        delete_for_account(account_id)
    except Exception as exc:
        logger.error(
            "notification_cleanup_failed",
            extra={"event": "notification_cleanup_failed", "exception_class": type(exc).__name__},
        )


def _expired(now: datetime) -> QuerySet[Notification]:
    return Notification.objects.filter(
        Q(created_at__lt=now - ANY_RETENTION)
        | Q(read_at__isnull=False, read_at__lt=now - READ_RETENTION)
    )


def purge_expired(now: datetime, dry_run: bool) -> int:
    """Read notifications read over 90 days ago and any created over 180 days ago.

    Deletes in batches of `PURGE_BATCH` ids per statement.
    """
    if dry_run:
        return _expired(now).count()
    total = 0
    while True:
        ids = list(_expired(now).order_by("seq").values_list("id", flat=True)[:PURGE_BATCH])
        if not ids:
            return total
        total += Notification.objects.filter(id__in=ids).delete()[0]
