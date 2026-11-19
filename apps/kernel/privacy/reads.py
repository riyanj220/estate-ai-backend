"""Reasoned private reads (AD-9): an administrator must explain every read of private data.

Every `admin_read_*` selector calls `record_private_read` as its first statement, before it
touches any data. The recorder fails closed: if the audit write fails the error propagates and
the selector returns nothing. Owning apps declare their private targets with
`register_private_target` (from `AppConfig.ready`).
"""

import re
import uuid
from typing import NamedTuple

from django.db import transaction

from apps.kernel import audit
from apps.kernel.actor import Actor, require_admin
from apps.kernel.privacy.patterns import LONG_DIGIT_RUN, reject_contact_details

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")  # same limit as audit target_type
REASON_MIN = 10
REASON_MAX = 300
READ_ACTION = audit.READ_ACTION

_targets: dict[str, str] = {}

audit.register_action(READ_ACTION, {})


class PrivateTargetRef(NamedTuple):
    target: str
    target_id: uuid.UUID


def register_private_target(name: str, description: str = "") -> None:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("private target name must be lowercase snake_case")
    if not isinstance(description, str):
        raise ValueError("description must be a string")
    if _targets.setdefault(name, description) != description:
        raise ValueError(f"private target {name!r} is already registered differently")


def registered_targets() -> dict[str, str]:
    return dict(_targets)


def record_private_read(actor: Actor, reason: str, target_ref: PrivateTargetRef | tuple) -> None:
    require_admin(actor)
    try:
        target, target_id = target_ref
    except (TypeError, ValueError):
        raise ValueError("target_ref must be (target, target_id)") from None
    if not isinstance(target, str) or target not in _targets:
        raise ValueError("unknown private target")
    if not isinstance(target_id, uuid.UUID):
        raise ValueError("target id must be a UUID")
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    reason = reason.strip()
    if not REASON_MIN <= len(reason) <= REASON_MAX:
        raise ValueError("reason must be 10 to 300 characters")
    if LONG_DIGIT_RUN.search(reason) or reject_contact_details(reason) is not None:
        raise ValueError("reason must not contain contact details")
    with transaction.atomic():
        audit._record_reasoned(actor, READ_ACTION, target, target_id, reason)
