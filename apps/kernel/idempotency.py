"""Idempotency store (AD-17): one claim per (scope, account, key) inside the service transaction.

A replay inside 24 hours returns the original result reference; a different request hash is
refused with `idempotency_key_reused`. Records between 24 hours and 7 days old are expired and
are reclaimed in place; `purge_expired` deletes them after 7 days (scheduled by Story 1.9).
Only a request hash and a `type:id` reference are stored, never a request or response body.

A claim that is not new and has no `result_ref` should not occur: claim and `complete` share one
transaction, so it arises only from a service bug or a missed `complete()`. Callers should treat it
as a conflict (409-style, retry later) rather than redo the work.
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from apps.kernel.errors import DomainError
from apps.kernel.models import IdempotencyRecord

TTL = timedelta(hours=24)
RETENTION = timedelta(days=7)

_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SCOPE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_REF = re.compile(r"^[a-z][a-z0-9_]{0,31}:[A-Za-z0-9._-]{1,64}$")

_INSERT = """
INSERT INTO kernel_idempotencyrecord (id, scope, account_id, key, request_hash, created_at)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (scope, account_id, key) DO NOTHING
RETURNING id
"""


@dataclass(frozen=True)
class Claim:
    is_new: bool
    result_ref: str | None
    _record_id: uuid.UUID

    def complete(self, result_ref: str) -> None:
        """Record what the first attempt produced. Only the claim that created the record may."""
        if not self.is_new:
            raise RuntimeError("only a new claim can be completed")
        _check_ref(result_ref)
        if not connection.in_atomic_block:
            raise RuntimeError("complete must run inside the service transaction")
        updated = IdempotencyRecord.objects.filter(
            pk=self._record_id, result_ref__isnull=True
        ).update(result_ref=result_ref)
        if updated != 1:
            raise RuntimeError("claim is already completed or no longer exists")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value


def _check_ref(result_ref: str) -> None:
    if not isinstance(result_ref, str) or not _REF.fullmatch(result_ref):
        raise ValueError("result_ref must look like 'type:id'")


def _json_default(value: Any) -> str:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return _aware(value).isoformat()
    raise TypeError(f"{type(value).__name__} is not fingerprintable")


def _check_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("mapping keys must be strings")
            _check_keys(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _check_keys(item)


def request_fingerprint(mapping: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON (sorted keys, no whitespace) of the request values.

    Supported: str, int, float (finite), bool, None, list, dict with string keys, UUID and aware
    datetime. Anything else raises TypeError or ValueError.
    """
    _check_keys(mapping)
    canonical = json.dumps(
        mapping,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def claim(
    scope: str,
    account_id: uuid.UUID,
    key: str,
    request_hash: str,
    now: datetime | None = None,
) -> Claim:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("key must be 1-128 characters from [A-Za-z0-9._:-]")
    if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
        raise ValueError("invalid scope")
    if not isinstance(account_id, uuid.UUID):
        raise ValueError("account_id must be a UUID")
    if not isinstance(request_hash, str) or not _HASH.fullmatch(request_hash):
        raise ValueError("request_hash must be a SHA-256 hex digest")
    now = _aware(now) if now is not None else timezone.now()
    if not connection.in_atomic_block:
        raise RuntimeError("claim must run inside the service transaction")

    for _ in range(3):
        record_id = uuid.uuid4()
        with connection.cursor() as cursor:
            cursor.execute(_INSERT, [record_id, scope, account_id, key, request_hash, now])
            inserted = cursor.fetchone()
        if inserted:
            return Claim(True, None, record_id)
        with transaction.atomic():  # savepoint: the row lock is held until the outer commit
            record = (
                IdempotencyRecord.objects.select_for_update()
                .filter(scope=scope, account_id=account_id, key=key)
                .first()
            )
            if record is None:
                continue  # purged between the insert and the read; claim again
            if record.created_at <= now - TTL:
                record.request_hash = request_hash
                record.result_ref = None
                record.created_at = now
                record.save(update_fields=["request_hash", "result_ref", "created_at"])
                return Claim(True, None, record.pk)
            if record.request_hash != request_hash:
                raise DomainError("idempotency_key_reused")
            return Claim(False, record.result_ref, record.pk)
    raise RuntimeError("could not claim idempotency key")


def purge_expired(now: datetime) -> int:
    """Delete records older than the retention window; returns how many."""
    deleted, _ = IdempotencyRecord.objects.filter(created_at__lt=_aware(now) - RETENTION).delete()
    return deleted
