"""Where an account's private values come from. Apps register a source from `AppConfig.ready`
(the kernel never imports an app):

    known_values.register("identity", identity_selectors.known_private_values)

A source that fails raises: text is never redacted with half the values and then sent.
"""

import uuid
from collections.abc import Callable

from apps.kernel.actor import Actor
from apps.kernel.ai.redaction import KnownValues

Source = Callable[[uuid.UUID], KnownValues]
_sources: dict[str, Source] = {}


def register(name: str, source: Source) -> None:
    if _sources.setdefault(name, source) is not source:
        raise ValueError(f"known-values source {name!r} is already registered")


def known_values_for(actor: Actor) -> KnownValues:
    """The actor's own values; a Visitor has none."""
    values = KnownValues()
    if actor.account_id is None:
        return values
    for source in list(_sources.values()):
        values += source(actor.account_id)
    return values
