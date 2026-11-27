"""Reads of `assistant`: only the owning account ever sees a session."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from apps.assistant.models import AssistantSession, AssistantTurn
from apps.kernel.actor import Actor, require_signed_in
from apps.kernel.errors import DomainError
from apps.listings import selectors as listings


@dataclass(frozen=True)
class SourceView:
    title: str
    source: str
    version: int


@dataclass(frozen=True)
class TurnView:
    id: uuid.UUID
    sequence: int
    client_turn_id: uuid.UUID
    message: str = field(repr=False)
    reply: str = field(repr=False)
    sources: tuple[SourceView, ...]
    created_at: datetime


@dataclass(frozen=True)
class SessionView:
    id: uuid.UUID
    listing_id: uuid.UUID
    registry_version: int
    schema_version: int
    created_at: datetime
    turns: tuple[TurnView, ...]


def turn_view(row: AssistantTurn) -> TurnView:
    return TurnView(
        id=row.id,
        sequence=row.sequence,
        client_turn_id=row.client_turn_id,
        message=row.user_text,
        reply=row.reply_text,
        sources=tuple(SourceView(**source) for source in row.sources),
        created_at=row.created_at,
    )


def session_view(row: AssistantSession) -> SessionView:
    return SessionView(
        id=row.id,
        listing_id=row.listing_id,
        registry_version=row.registry_version,
        schema_version=row.schema_version,
        created_at=row.created_at,
        turns=tuple(turn_view(turn) for turn in row.turns.exclude(reply_text="")),
    )


def owned_session(actor: Actor, session_id) -> AssistantSession:
    """The caller's own session about a Listing it can still see; anything else is `not_found`."""
    require_signed_in(actor)
    key = listings.canonical_uuid(session_id)
    row = (
        AssistantSession.objects.filter(id=key, account_id=actor.account_id).first()
        if key
        else None
    )
    if row is None:
        raise DomainError("not_found")
    listings.get_owned_listing(actor, row.listing_id)
    return row


def get_session(actor: Actor, session_id) -> SessionView:
    return session_view(owned_session(actor, session_id))
