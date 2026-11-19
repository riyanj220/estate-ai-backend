"""Audit trail (AD-9): append-only records written in the caller's own transaction.

Owning apps declare each action once with `register_action(action, schema)` (from
`AppConfig.ready`). A schema maps every allowed metadata key to `bool`, `int`, `uuid.UUID` or a
collection of allowed strings, never free text. `record` refuses anything else before writing,
so contact details and message text cannot be smuggled in. It never swallows its own errors.
"""

import re
import uuid
from collections.abc import Collection, Mapping
from typing import Any

from django.db import connection
from django.utils import timezone

from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord

_ACTION = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
DIGIT_RUN = re.compile(r"\d{7,}")
_SCALARS = (bool, int, uuid.UUID)
READ_ACTION = "privacy.viewed"  # written only by kernel.privacy.reads
REASON_MAX = 300

_actions: dict[str, dict[str, Any]] = {}


def _normalise(schema: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, Mapping):
        raise ValueError("schema must be a mapping")
    out: dict[str, Any] = {}
    for key, kind in schema.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError("metadata keys must be lowercase snake_case")
        if kind in _SCALARS:
            out[key] = kind
        elif isinstance(kind, Collection) and not isinstance(kind, str | bytes) and kind:
            try:
                values = frozenset(kind)
            except TypeError:
                raise ValueError("enumeration values must be strings") from None
            if not all(isinstance(v, str) and v and not DIGIT_RUN.search(v) for v in values):
                raise ValueError("enumeration values must be short strings without digit runs")
            out[key] = values
        else:
            raise ValueError("a metadata type must be bool, int, uuid.UUID or an enumeration")
    return out


def register_action(action: str, schema: Mapping[str, Any]) -> None:
    if not isinstance(action, str) or not _ACTION.fullmatch(action) or len(action) > 64:
        raise ValueError("action must look like domain.past_tense")
    definition = _normalise(schema)
    if _actions.setdefault(action, definition) != definition:
        raise ValueError(f"action {action!r} is already registered with a different schema")


def registered_actions() -> dict[str, dict[str, Any]]:
    return dict(_actions)


def _validate(action: str, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    schema = _actions.get(action) if isinstance(action, str) else None
    if schema is None:
        raise ValueError("unregistered audit action")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    if set(metadata) != set(schema):
        raise ValueError("metadata keys do not match the registered schema")
    clean: dict[str, Any] = {}
    for key, kind in schema.items():
        value = metadata[key]
        if isinstance(kind, frozenset):
            if not isinstance(value, str) or value not in kind or DIGIT_RUN.search(value):
                raise ValueError(f"metadata {key!r} is not an allowed value")
            clean[key] = value
        elif kind is bool:
            if not isinstance(value, bool):
                raise ValueError(f"metadata {key!r} must be a bool")
            clean[key] = value
        elif kind is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"metadata {key!r} must be an int")
            if DIGIT_RUN.search(str(abs(value))):
                raise ValueError(f"metadata {key!r} looks like contact details")
            clean[key] = value
        else:
            if not isinstance(value, uuid.UUID):
                raise ValueError(f"metadata {key!r} must be a UUID")
            clean[key] = str(value)
    return clean


def record(
    actor: Actor,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    metadata: Mapping[str, Any] | None = None,
) -> AuditRecord:
    """Write one audit record in the caller's open transaction."""
    if action == READ_ACTION:
        raise ValueError("this action is written only by record_private_read")
    return _write(actor, action, target_type, target_id, metadata, "")


def _record_reasoned(
    actor: Actor, action: str, target_type: str, target_id: uuid.UUID, reason: str
) -> AuditRecord:
    """Module-private: only `kernel.privacy.reads` writes a reason."""
    if (
        not isinstance(reason, str)
        or len(reason) > REASON_MAX
        or not reason.isprintable()
        or "\x00" in reason
    ):
        raise ValueError("invalid reason")
    return _write(actor, action, target_type, target_id, None, reason)


def _write(
    actor: Actor,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    metadata: Mapping[str, Any] | None,
    reason: str,
) -> AuditRecord:
    if not connection.in_atomic_block:
        raise RuntimeError("audit.record must run inside an open transaction")
    if not isinstance(actor, Actor):
        raise ValueError("actor must be an Actor")
    if not isinstance(target_type, str) or not _KEY.fullmatch(target_type):
        raise ValueError("target_type must be lowercase snake_case")
    if not isinstance(target_id, uuid.UUID):
        raise ValueError("target_id must be a UUID")
    clean = _validate(action, metadata)
    return AuditRecord.objects.create(
        actor_id=actor.account_id,
        actor_kind=actor.kind.value,
        action=action,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        request_id=(actor.request_id or "")[:64],
        metadata=clean,
        created_at=timezone.now(),
    )
