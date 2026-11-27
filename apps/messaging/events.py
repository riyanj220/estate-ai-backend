"""Domain events emitted by `messaging` (AD-18). Ids and numbers only, never message text."""

import uuid
from dataclasses import dataclass

from apps.kernel.events import DomainEvent


@dataclass(frozen=True)
class MessageSent(DomainEvent):
    """A Message was saved, after commit. The recipients are the two participants as the
    Conversation row names them (AD-13); live delivery (Story 7.3) subscribes to this."""

    name = "messaging.message_sent"
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    seq: int
    sender_account_id: uuid.UUID
    recipient_account_ids: tuple[uuid.UUID, ...]
