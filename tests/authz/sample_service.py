"""Sample mutating service showing the AD-22 shape: `actor` first, policy enforced inside.

Tests only; no domain code is invented for this story.
"""

from dataclasses import dataclass
from uuid import UUID

from apps.kernel.actor import Actor, require_signed_in
from apps.kernel.errors import DomainError


@dataclass
class Widget:
    id: UUID
    owner_id: UUID
    name: str


STORE: dict[UUID, Widget] = {}


def _owned(actor: Actor, widget_id: UUID) -> Widget:
    widget = STORE.get(widget_id)
    if widget is None or widget.owner_id != actor.account_id:
        raise DomainError("not_found")  # same answer whether it exists or not
    return widget


def get_widget(actor: Actor, widget_id: UUID) -> Widget:
    require_signed_in(actor)
    return _owned(actor, widget_id)


def rename_widget(actor: Actor, widget_id: UUID, name: str) -> Widget:
    require_signed_in(actor)
    widget = _owned(actor, widget_id)
    widget.name = name
    return widget
