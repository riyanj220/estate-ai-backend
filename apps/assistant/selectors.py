"""Reads of `assistant`: only the owning account ever sees a session."""

import uuid
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime

from apps.assistant import planner
from apps.assistant.facts import Fact, facts_of
from apps.assistant.models import AssistantSession, AssistantTurn
from apps.assistant.planner import NextStep
from apps.kernel.actor import Actor, require_signed_in
from apps.kernel.errors import DomainError
from apps.listings import selectors as listings


@dataclass(frozen=True)
class SourceView:
    title: str
    source: str
    version: int


@dataclass(frozen=True)
class AppliedView:
    field: str
    value: str | int | bool | None = dataclass_field(repr=False)


@dataclass(frozen=True)
class RejectedView:
    field: str
    code: str


@dataclass(frozen=True)
class TurnView:
    id: uuid.UUID
    sequence: int
    client_turn_id: uuid.UUID
    message: str = dataclass_field(repr=False)
    reply: str = dataclass_field(repr=False)
    sources: tuple[SourceView, ...]
    created_at: datetime
    applied: tuple[AppliedView, ...] = ()
    rejected: tuple[RejectedView, ...] = ()
    conflicts: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionView:
    id: uuid.UUID
    listing_id: uuid.UUID
    registry_version: int
    schema_version: int
    created_at: datetime
    turns: tuple[TurnView, ...]
    next_step: NextStep | None = None
    summary: tuple[Fact, ...] = ()


def turn_view(row: AssistantTurn) -> TurnView:
    return TurnView(
        id=row.id,
        sequence=row.sequence,
        client_turn_id=row.client_turn_id,
        message=row.user_text,
        reply=row.reply_text,
        sources=tuple(SourceView(**source) for source in row.sources),
        created_at=row.created_at,
        applied=tuple(AppliedView(**item) for item in row.result.get("applied", ())),
        rejected=tuple(RejectedView(**item) for item in row.result.get("rejected", ())),
        conflicts=tuple(row.result.get("conflicts", ())),
        notices=tuple(row.result.get("notices", ())),
    )


def session_view(row: AssistantSession, listing: listings.ListingView) -> SessionView:
    return SessionView(
        id=row.id,
        listing_id=row.listing_id,
        registry_version=row.registry_version,
        schema_version=row.schema_version,
        created_at=row.created_at,
        turns=tuple(turn_view(turn) for turn in row.turns.exclude(reply_text="")),
        next_step=planner.next_step(listing),
        summary=facts_of(listing),
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
    row = owned_session(actor, session_id)
    return session_view(row, listings.get_owned_listing(actor, row.listing_id))
