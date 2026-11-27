"""The `AccountDataEraser` registry (AD-23, Stories 10.2 and 10.3).

Each domain app that holds an account's data registers one eraser from `AppConfig.ready`. The
closure use case in `governance` calls every one of them, in layer order from the top layer
down, inside its own transaction. An eraser writes its owner's audit record, deletes files only
after commit, and never deletes quota, moderation or audit history.
"""

from typing import Protocol
from uuid import UUID

from apps.kernel.actor import Actor

# The apps in layer order, highest first (the import-linter layers; tier-4 apps are peers).
LAYER_ORDER = (
    "governance",
    "messaging",
    "assistant",
    "valuation",
    "search",
    "listings",
    "identity",
    "catalog",
)


class AccountDataEraser(Protocol):
    def __call__(self, actor: Actor, account_id: UUID) -> None:
        """Erase this app's share of the account, inside the caller's transaction."""


_erasers: dict[str, AccountDataEraser] = {}


def register(app: str, eraser: AccountDataEraser) -> None:
    if app not in LAYER_ORDER:
        raise ValueError(f"unknown app {app!r}")
    if _erasers.setdefault(app, eraser) is not eraser:
        raise ValueError(f"app {app!r} already has an eraser")


def registered() -> list[str]:
    """The apps with an eraser, in the order they are called."""
    return sorted(_erasers, key=LAYER_ORDER.index)


def erase_all(actor: Actor, account_id: UUID) -> None:
    for app in registered():
        _erasers[app](actor, account_id)
