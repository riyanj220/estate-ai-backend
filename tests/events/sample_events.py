from dataclasses import dataclass
from uuid import UUID

from apps.kernel.events import DomainEvent


@dataclass(frozen=True)
class ThingHappened(DomainEvent):
    name = "thing.happened"
    thing_id: UUID


@dataclass(frozen=True)
class OtherThingHappened(DomainEvent):
    name = "thing.other_happened"
