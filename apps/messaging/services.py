"""Write path for Conversations and Messages. Only this module creates or changes them.

One transaction per send: lock the sender's account (which serialises its sends and starts),
check it may send, claim the `Idempotency-Key`, lock the Conversation, check it is writable,
count the rate limits, number and store the Message. A refusal rolls all of it back. Message
text is never logged, audited or put in an event.
"""

import uuid
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest, Least
from django.utils import timezone

from apps.identity.capabilities import Access, Capability, matrix_access
from apps.identity.selectors import ADMIN_ACCOUNT_TYPE, lock_account
from apps.identity.state import AccountState
from apps.kernel import idempotency, ratelimit
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.kernel.events import emit
from apps.listings import selectors as listings
from apps.messaging import selectors
from apps.messaging.events import MessageSent
from apps.messaging.models import MAX_BODY, Conversation, Message

SCOPE = "message.send"


class MessageInvalid(DomainError):
    """`validation_error` with a stable code per field; never the submitted value."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("validation_error")
        self.fields = fields


@dataclass(frozen=True)
class SendResult:
    conversation: selectors.ConversationView
    message: selectors.MessageView | None  # None: an existing thread that is read-only
    created: bool  # a new Conversation


def _clean(body, key) -> str:
    problems = {}
    text = ""
    if body is None:
        problems["body"] = "required"
    elif not isinstance(body, str) or "\x00" in body or not _encodable(body):
        problems["body"] = "invalid"
    else:
        text = body.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            problems["body"] = "blank"
        elif len(text) > MAX_BODY:
            problems["body"] = "max_length"
    if key is None:
        problems["idempotency_key"] = "required"
    elif not isinstance(key, str) or not idempotency.is_key(key):
        problems["idempotency_key"] = "invalid"
    if problems:
        raise MessageInvalid(problems)
    return text


def _encodable(text: str) -> bool:
    try:
        text.encode()
    except UnicodeEncodeError:  # a lone surrogate cannot be stored
        return False
    return True


def _lock_sender(actor: Actor) -> None:
    """FR-74: only an Active Individual or Agency account sends."""
    account = lock_account(actor.account_id)
    if account.type == ADMIN_ACCOUNT_TYPE:
        raise DomainError("forbidden")
    if matrix_access(account.state, Capability.SEND_MESSAGES) is not Access.ALLOWED:
        if account.state is AccountState.EMAIL_UNVERIFIED:
            raise DomainError(selectors.EMAIL_UNVERIFIED)
        raise DomainError(selectors.ACCOUNT_RESTRICTED)


def _require_sender(actor: Actor) -> None:
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER:
        raise DomainError("forbidden")


def _claim(actor: Actor, key: str, target: uuid.UUID, text: str):
    return idempotency.claim(
        SCOPE,
        actor.account_id,
        key,
        idempotency.request_fingerprint({"target": target, "body": text}),
    )


def _replayed(actor: Actor, result_ref: str | None) -> SendResult:
    """What the first attempt produced, as it reads now."""
    kind, _, found = (result_ref or "").partition(":")
    message = Message.objects.filter(id=found).first() if kind == "message" else None
    if message is not None:
        conversation_id = message.conversation_id
    elif kind == "conversation":
        conversation_id = found
    else:
        raise DomainError("conflict")
    return SendResult(
        selectors.get_conversation(actor, conversation_id),
        None if message is None else selectors.message_view(actor, message),
        created=False,
    )


def _store(actor: Actor, row: Conversation, text: str, key: str) -> Message:
    """Number and save one Message in a locked (or just created) Conversation."""
    earlier = Message.objects.filter(
        conversation_id=row.id, sender_account_id=actor.account_id, client_message_id=key
    ).first()
    if earlier is not None:  # a retry after the idempotency record expired
        if earlier.body != text:
            raise DomainError("idempotency_key_reused")
        return earlier
    ratelimit.enforce("message_send", ratelimit.derive_key("account", str(actor.account_id)))
    now = timezone.now()
    row.last_seq += 1
    row.last_message_at = now
    mine = _read_field(actor, row)
    setattr(row, mine, row.last_seq)  # one has read what one wrote, and what came before it
    row.save(update_fields=["last_seq", "last_message_at", mine])
    message = Message.objects.create(
        conversation=row,
        seq=row.last_seq,
        sender_account_id=actor.account_id,
        body=text,
        client_message_id=key,
        created_at=now,
    )
    emit(
        MessageSent(
            conversation_id=row.id,
            message_id=message.id,
            seq=message.seq,
            sender_account_id=actor.account_id,
            recipient_account_ids=(row.initiator_account_id, row.owner_account_id),
        )
    )
    return message


def _read_field(actor: Actor, row: Conversation) -> str:
    """The column holding the caller's read mark."""
    if actor.account_id == row.initiator_account_id:
        return "initiator_read_seq"
    return "owner_read_seq"


def mark_read(actor: Actor, conversation_id, seq: int) -> None:
    """The caller has read its Conversation up to `seq` (FR-50). The mark never goes back and
    never passes the newest Message. Anyone else gets `not_found`."""
    row = selectors.participating(actor, conversation_id)
    field = _read_field(actor, row)
    Conversation.objects.filter(id=row.id).update(
        **{field: Greatest(F(field), Least(Value(seq), F("last_seq")))}
    )


def start_conversation(actor: Actor, listing_id, body, idempotency_key) -> SendResult:
    """Send an inquiry about a Listing (FR-48). There is one Conversation per Listing and
    initiating account: a second start sends into it, or, when it is read-only because of the
    Listing or the other account, just opens it (`message` None).

    Refusals: Visitor `unauthenticated`; administrator `forbidden`; invalid body or key
    `validation_error`; `messaging_email_unverified` / `messaging_unavailable`; a Listing that is
    not public and has no Conversation of the caller's `not_found`; the caller's own Listing
    `own_listing`; over a limit `throttled`."""
    _require_sender(actor)
    text = _clean(body, idempotency_key)
    key = listings.canonical_uuid(listing_id)
    if key is None:
        raise DomainError("not_found")
    with transaction.atomic():
        _lock_sender(actor)
        claim = _claim(actor, idempotency_key, key, text)
        if not claim.is_new:
            return _replayed(actor, claim.result_ref)
        row = (
            Conversation.objects.select_for_update()
            .filter(listing_id=key, initiator_account_id=actor.account_id)
            .first()
        )
        created = row is None
        if row is None:
            owner = listings.public_owner_id(Actor.system(actor.request_id), key)
            if owner == actor.account_id:
                raise DomainError("own_listing")
            ratelimit.enforce(
                "conversation_new", ratelimit.derive_key("account", str(actor.account_id))
            )
            row = Conversation.objects.create(
                listing_id=key, initiator_account_id=actor.account_id, owner_account_id=owner
            )
        elif selectors.view_of(actor, row).read_only_reason is not None:
            claim.complete(f"conversation:{row.id}")
            return SendResult(selectors.view_of(actor, row), None, created=False)
        message = _store(actor, row, text, idempotency_key)
        claim.complete(f"message:{message.id}")
        return SendResult(
            selectors.view_of(actor, row), selectors.message_view(actor, message), created
        )


def send_message(actor: Actor, conversation_id, body, idempotency_key) -> selectors.MessageView:
    """Either participant sends into an existing Conversation (FR-49, FR-53).

    Refusals: as `start_conversation` for the sender; a Conversation the caller is not part of
    `not_found`; `counterpart_unavailable` or `conversation_read_only` when it takes no more
    Messages; over the limit `throttled`."""
    _require_sender(actor)
    text = _clean(body, idempotency_key)
    with transaction.atomic():
        _lock_sender(actor)
        found = selectors.participating(actor, conversation_id)
        claim = _claim(actor, idempotency_key, found.id, text)
        if not claim.is_new:
            replayed = _replayed(actor, claim.result_ref).message
            if replayed is None:
                raise DomainError("conflict")
            return replayed
        row = Conversation.objects.select_for_update().get(id=found.id)
        reason = selectors.view_of(actor, row).read_only_reason
        if reason is not None:
            raise DomainError(reason)
        message = _store(actor, row, text, idempotency_key)
        claim.complete(f"message:{message.id}")
        return selectors.message_view(actor, message)
