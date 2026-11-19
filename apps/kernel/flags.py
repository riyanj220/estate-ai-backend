"""Feature flags (AD-20): declared in code with a default, overridden in the database.

Declaring in code makes a typo fail loudly: reading or setting an undeclared name raises.
If the database cannot be read, `is_enabled` logs the exception class and returns the declared
default (fail to default), so a database fault does not break every caller.
Who changed a flag and when is recorded on the row; a full history arrives with the audit
trail (Story 1.10). Apps declare their flags from `AppConfig.ready`.
"""

import logging
import re
from datetime import datetime

from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.kernel.models import FeatureFlag

logger = logging.getLogger("estate.flags")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_declared: dict[str, tuple[bool, str]] = {}


class UndeclaredFlag(KeyError):
    """The flag was never declared with `register_flag`."""


def register_flag(name: str, default: bool, description: str) -> None:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("flag name must be lowercase snake_case")
    if not isinstance(default, bool):
        raise ValueError("default must be a bool")
    definition = (default, description)
    if _declared.setdefault(name, definition) != definition:
        raise ValueError(f"flag {name!r} is already declared differently")


def _default(name: str) -> bool:
    try:
        return _declared[name][0]
    except (KeyError, TypeError):
        raise UndeclaredFlag(name) from None


def is_enabled(name: str) -> bool:
    default = _default(name)
    try:
        with transaction.atomic():  # savepoint: a failed read must not poison the caller
            state = FeatureFlag.objects.filter(name=name).values_list("enabled", flat=True).first()
    except DatabaseError as exc:
        logger.warning(
            "flag_store_failure",
            extra={"event": "flag_store_failure", "exception_class": type(exc).__name__},
        )
        return default
    return default if state is None else state


def set_flag(actor: Actor, name: str, enabled: bool, now: datetime | None = None) -> None:
    require_signed_in(actor)
    if actor.kind not in (ActorKind.ADMIN, ActorKind.SYSTEM):
        raise DomainError("not_found")
    _default(name)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a bool")
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("now must be timezone-aware")
    FeatureFlag.objects.update_or_create(
        name=name,
        defaults={
            "enabled": enabled,
            "changed_by": actor.account_id,
            "changed_at": now or timezone.now(),
        },
    )
