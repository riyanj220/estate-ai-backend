"""Read path for Conversations (FR-51). Only the two participants may read; everyone else, an
administrator included, gets `not_found` (a Visitor `unauthenticated`)."""

import base64
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import Count, F, Q

from apps.identity import selectors as identity
from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.identity.state import AccountState
from apps.kernel.actor import Actor, ActorKind, require_admin, require_signed_in
from apps.kernel.errors import DomainError
from apps.listings import selectors as listings
from apps.messaging.models import Conversation, Message

MESSAGE_PAGE = 200
REPORT_SELECTED_MAX = 10
REPORT_CONTEXT = 5  # Messages shown before the earliest reported one (FR-54)
INBOX_PAGE = 20

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
    unread_count: int  # Messages from the other participant above the caller's read mark


@dataclass(frozen=True)
class InboxPage:
    items: tuple[ConversationView, ...]
    next: str | None  # the cursor of the following page


class InvalidCursor(ValueError):
    pass


class InvalidSelection(ValueError):
    """A reported Message is not one the other participant sent in this Conversation."""


@dataclass(frozen=True)
class EvidenceMessage:
    seq: int
    from_reporter: bool
    selected: bool
    body: str = field(repr=False)
    created_at: datetime


@dataclass(frozen=True)
class Evidence:
    """What a Conversation Report keeps (Story 9.5): never the rest of the thread."""

    conversation_id: uuid.UUID
    listing_id: uuid.UUID
    other_account_id: uuid.UUID
    messages: tuple[EvidenceMessage, ...]


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
    return views_of(actor, [row])[0]


def _own_refusal(actor: Actor) -> str | None:
    """Why the caller's own account may not send, if it may not."""
    if can(actor, Capability.SEND_MESSAGES):
        return None
    own = identity.get_account(actor.account_id)
    unverified = own is not None and own.state is AccountState.EMAIL_UNVERIFIED
    return EMAIL_UNVERIFIED if unverified else ACCOUNT_RESTRICTED


def views_of(actor: Actor, rows: list[Conversation]) -> list[ConversationView]:
    """The rows as the caller sees them, in the same number of queries for one or many."""
    if not rows:
        return []
    own = _own_refusal(actor)
    others = identity.participants({counterpart_id(row, actor.account_id) for row in rows})
    headers = listings.listing_headers({row.listing_id for row in rows})
    views = []
    for row in rows:
        other = others[counterpart_id(row, actor.account_id)]
        header = headers[row.listing_id]
        mine = actor.account_id == row.initiator_account_id
        if own is not None:
            reason = own
        elif other.deactivated:
            reason = COUNTERPART_GONE
        elif header.availability != listings.AVAILABLE:
            reason = LISTING_GONE
        else:
            reason = None
        views.append(
            ConversationView(
                id=row.id,
                listing_id=row.listing_id,
                listing_availability=header.availability,
                listing_title=header.title,
                role=ROLE_INITIATOR if mine else ROLE_OWNER,
                counterpart_name=other.name,
                read_only_reason=reason,
                last_seq=row.last_seq,
                last_message_at=row.last_message_at,
                created_at=row.created_at,
                unread_count=max(
                    0, row.last_seq - (row.initiator_read_seq if mine else row.owner_read_seq)
                ),
            )
        )
    return views


def _encode_cursor(row: Conversation) -> str:
    return base64.urlsafe_b64encode(f"{row.last_message_at.isoformat()}|{row.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        if len(cursor) > 200:
            raise ValueError
        at, _, key = base64.urlsafe_b64decode(cursor.encode("ascii")).decode().partition("|")
        position = datetime.fromisoformat(at), uuid.UUID(key)
    except ValueError:  # covers bad base64 and bad text
        raise InvalidCursor from None
    if position[0].tzinfo is None:
        raise InvalidCursor
    return position


def inbox(actor: Actor, cursor: str | None = None) -> InboxPage:
    """The caller's Conversations, as either participant, latest activity first (FR-50)."""
    require_reader(actor)
    rows = Conversation.objects.filter(
        Q(initiator_account_id=actor.account_id) | Q(owner_account_id=actor.account_id)
    )
    if cursor:
        at, key = _decode_cursor(cursor)
        rows = rows.filter(Q(last_message_at__lt=at) | Q(last_message_at=at, id__lt=key))
    found = list(rows.order_by("-last_message_at", "-id")[: INBOX_PAGE + 1])
    page = found[:INBOX_PAGE]
    following = _encode_cursor(page[-1]) if len(found) > INBOX_PAGE else None
    return InboxPage(tuple(views_of(actor, page)), following)


def unread_conversations(actor: Actor) -> int:
    """How many of the caller's Conversations hold something unread."""
    require_reader(actor)
    return Conversation.objects.filter(
        Q(initiator_account_id=actor.account_id, last_seq__gt=F("initiator_read_seq"))
        | Q(owner_account_id=actor.account_id, last_seq__gt=F("owner_read_seq"))
    ).count()


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


def report_evidence(actor: Actor, conversation_id, message_ids) -> Evidence:
    """For `governance`: the other participant's Messages with these ids, plus up to
    `REPORT_CONTEXT` Messages sent just before the earliest of them. Only a participant may ask
    (`not_found` otherwise); an id that is not such a Message raises `InvalidSelection`."""
    row = participating(actor, conversation_id)
    wanted = set(message_ids)
    thread = Message.objects.filter(conversation_id=row.id)
    other = counterpart_id(row, actor.account_id)
    selected = list(thread.filter(id__in=wanted, sender_account_id=other).order_by("seq"))
    if not wanted or len(selected) != len(wanted):
        raise InvalidSelection
    before = list(thread.filter(seq__lt=selected[0].seq).order_by("-seq")[:REPORT_CONTEXT])
    return Evidence(
        row.id,
        row.listing_id,
        other,
        tuple(
            EvidenceMessage(
                m.seq,
                m.sender_account_id == actor.account_id,
                m.id in wanted,
                m.body,
                m.created_at,
            )
            for m in [*reversed(before), *selected]
        ),
    )


def inquiries_received(actor: Actor) -> int:
    """Conversations others started about the caller's Listings (Story 8.1)."""
    require_reader(actor)
    return Conversation.objects.filter(owner_account_id=actor.account_id).count()


def inquiries_by_listing(actor: Actor, listing_ids) -> dict[uuid.UUID, int]:
    """The same, per Listing, in one query; a Listing without any is 0."""
    require_reader(actor)
    ids = list(listing_ids)
    counted = dict(
        Conversation.objects.filter(owner_account_id=actor.account_id, listing_id__in=ids)
        .values_list("listing_id")
        .annotate(total=Count("id"))
        .order_by()
    )
    return {key: counted.get(key, 0) for key in ids}


def messages_sent_since(actor: Actor, since: datetime) -> int:
    """A platform total for the administrator dashboard (Story 10.8): a count, never content."""
    require_admin(actor)
    return Message.objects.filter(created_at__gte=since).count()
