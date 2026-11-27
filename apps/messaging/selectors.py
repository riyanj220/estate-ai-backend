"""Read path for Conversations (FR-51). Only the two participants may read; everyone else, an
administrator included, gets `not_found` (a Visitor `unauthenticated`)."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from apps.identity import selectors as identity
from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.identity.state import AccountState
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.listings import selectors as listings
from apps.messaging.models import Conversation, Message

MESSAGE_PAGE = 200

ROLE_INITIATOR = "initiator"
ROLE_OWNER = "owner"

# why a participant cannot send; also the error code of a refused send
EMAIL_UNVERIFIED = "messaging_email_unverified"
ACCOUNT_RESTRICTED = "messaging_unavailable"
COUNTERPART_GONE = "counterpart_unavailable"
LISTING_GONE = "conversation_read_only"
READ_ONLY_REASONS = (EMAIL_UNVERIFIED, ACCOUNT_RESTRICTED, COUNTERPART_GONE, LISTING_GONE)


@dataclass(frozen=True)
class ConversationView:
    """A Conversation as ONE participant sees it. No account id, email or phone."""

    id: uuid.UUID
    listing_id: uuid.UUID
    listing_availability: str
    listing_title: str | None  # only while the Listing is publicly visible
    role: str
    counterpart_name: str | None  # None: the other account is Deactivated
    read_only_reason: str | None  # None: the caller may send
    last_seq: int
    last_message_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class MessageView:
    id: uuid.UUID
    conversation_id: uuid.UUID
    seq: int
    body: str
    mine: bool
    client_message_id: str | None  # the caller's own Messages only
    created_at: datetime


@dataclass(frozen=True)
class MessagePage:
    items: tuple[MessageView, ...]
    has_more: bool


def require_reader(actor: Actor) -> None:
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, Capability.READ_CONVERSATIONS):
        raise DomainError("not_found")


def participating(actor: Actor, conversation_id) -> Conversation:
    """The row, only for one of its two participants. For `messaging` itself."""
    require_reader(actor)
    key = listings.canonical_uuid(conversation_id)
    row = Conversation.objects.filter(id=key).first() if key else None
    if row is None or actor.account_id not in (row.initiator_account_id, row.owner_account_id):
        raise DomainError("not_found")
    return row


def counterpart_id(row: Conversation, account_id: uuid.UUID) -> uuid.UUID:
    if account_id == row.initiator_account_id:
        return row.owner_account_id
    return row.initiator_account_id


def view_of(actor: Actor, row: Conversation) -> ConversationView:
    other = identity.participant(counterpart_id(row, actor.account_id))
    header = listings.listing_header(row.listing_id)
    if not can(actor, Capability.SEND_MESSAGES):
        own = identity.get_account(actor.account_id)
        unverified = own is not None and own.state is AccountState.EMAIL_UNVERIFIED
        reason = EMAIL_UNVERIFIED if unverified else ACCOUNT_RESTRICTED
    elif other.deactivated:
        reason = COUNTERPART_GONE
    elif header.availability != listings.AVAILABLE:
        reason = LISTING_GONE
    else:
        reason = None
    return ConversationView(
        id=row.id,
        listing_id=row.listing_id,
        listing_availability=header.availability,
        listing_title=header.title,
        role=ROLE_INITIATOR if actor.account_id == row.initiator_account_id else ROLE_OWNER,
        counterpart_name=other.name,
        read_only_reason=reason,
        last_seq=row.last_seq,
        last_message_at=row.last_message_at,
        created_at=row.created_at,
    )


def message_view(actor: Actor, row: Message) -> MessageView:
    mine = row.sender_account_id == actor.account_id
    return MessageView(
        id=row.id,
        conversation_id=row.conversation_id,
        seq=row.seq,
        body=row.body,
        mine=mine,
        client_message_id=row.client_message_id if mine else None,
        created_at=row.created_at,
    )


def get_conversation(actor: Actor, conversation_id) -> ConversationView:
    return view_of(actor, participating(actor, conversation_id))


def own_inquiry(actor: Actor, listing_id) -> ConversationView | None:
    """The Conversation the caller started about this Listing, if any."""
    require_reader(actor)
    key = listings.canonical_uuid(listing_id)
    row = (
        Conversation.objects.filter(listing_id=key, initiator_account_id=actor.account_id).first()
        if key
        else None
    )
    return None if row is None else view_of(actor, row)


def messages_after(actor: Actor, conversation_id, after: int = 0) -> MessagePage:
    """The Messages with `seq` above `after`, oldest first: the history and the resume read."""
    row = participating(actor, conversation_id)
    found = list(
        Message.objects.filter(conversation_id=row.id, seq__gt=after).order_by("seq")[
            : MESSAGE_PAGE + 1
        ]
    )
    return MessagePage(
        tuple(message_view(actor, m) for m in found[:MESSAGE_PAGE]), len(found) > MESSAGE_PAGE
    )
