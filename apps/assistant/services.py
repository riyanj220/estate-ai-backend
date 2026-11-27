"""The write path of the Assistant (Story 5.8): sessions and turns.

A turn calls the model once: the same answer carries the reply and the extracted fields. A
message that looks like a question also embeds it once for knowledge retrieval. Only redacted
text is stored, and only for a turn that was answered. No Listing value is read into the prompt,
and the Listing changes only through `listings.services.apply_updates` (`extraction`).
"""

import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.assistant import description as describing
from apps.assistant import extraction as extracting
from apps.assistant import planner, prompts
from apps.assistant.facts import Fact, facts_of
from apps.assistant.models import AssistantSession, AssistantTurn
from apps.assistant.selectors import (
    SessionView,
    TurnView,
    owned_session,
    session_view,
    turn_view,
)
from apps.catalog import extraction, registry
from apps.catalog import selectors as catalog
from apps.kernel import ratelimit
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway
from apps.kernel.ai.known_values import known_values_for
from apps.kernel.ai.ports import LLMRequest, Message
from apps.kernel.ai.redaction import KnownValues, RedactedText, redact
from apps.kernel.errors import DomainError
from apps.listings import services as listings
from apps.listings.lifecycle import Origin
from apps.listings.selectors import ListingView, canonical_uuid

logger = logging.getLogger("estate.assistant")

MAX_MESSAGE = gateway.ASSISTANT_TURN.max_chars
MAX_REPLY = 4000
MAX_TURNS = 200  # per session: history must not grow without bound
HISTORY_TURNS = 6  # earlier turns the model sees
ABANDONED_AFTER = timedelta(seconds=90)  # longer than retrieval plus the model call can take
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\ud800-\udfff]")
# Only a message that asks something is worth an embedding request on a free tier.
_QUESTION = re.compile(
    r"[?؟]"
    r"|\b(explain|meaning|means?|difference|define|matlab|kya)\b"
    r"|^\s*(what|how|why|which|when|where|who|is|are|can|does|do)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TurnResult:
    """`turn` is None when the model could not answer: nothing was stored and `message` says why.
    On success `message` is empty unless explanations were unavailable, and `next_step` is what
    the Draft still needs (Story 5.10)."""

    outcome: str
    message: str
    turn: TurnView | None
    next_step: planner.NextStep | None = None
    summary: tuple[Fact, ...] = ()  # what the Draft holds now (Story 5.11)


@dataclass(frozen=True)
class DescriptionResult:
    """`listing` is None when nothing was written; `message` then says why."""

    outcome: str
    message: str
    listing: ListingView | None = None
    claims: tuple[describing.Claim, ...] = ()
    left_out: tuple[str, ...] = ()  # fields not used because they are not confirmed


def open_session(actor: Actor, listing_id) -> tuple[SessionView, bool]:
    """The open session of the caller's Draft, created when there is none; True when created.

    Refusals: Visitor `unauthenticated`; not owned `not_found`; no `edit_drafts` capability
    (Suspended, Deactivated) `forbidden`; a Listing that is not a Draft `conflict`."""
    listing = listings.require_draft(actor, listing_id)
    mine = AssistantSession.objects.filter(listing_id=listing.id, closed_at__isnull=True)
    row = mine.first()
    if row is not None:
        return session_view(row, listing), False
    try:
        with transaction.atomic():
            row = AssistantSession.objects.create(
                account_id=actor.account_id,
                listing_id=listing.id,
                registry_version=registry.registry_version(),
                schema_version=extraction.SCHEMA_VERSION,
                created_at=timezone.now(),
            )
    except IntegrityError:  # a concurrent request opened it
        return session_view(mine.get(), listing), False
    return session_view(row, listing), True


def _check(message, client_turn_id) -> tuple[str, object]:
    errors = {}
    text = message.strip() if isinstance(message, str) else ""
    if message is None or (isinstance(message, str) and not text):
        errors["message"] = "required"
    elif not isinstance(message, str) or _CONTROL.search(text):
        errors["message"] = "invalid"
    elif len(text) > MAX_MESSAGE:
        errors["message"] = "too_long"
    key = canonical_uuid(client_turn_id)
    if key is None:
        errors["client_turn_id"] = "required" if client_turn_id is None else "invalid"
    if errors:
        raise listings.FieldsInvalid(errors)
    return text, key


def _claim(session, client_turn_id, text: str) -> tuple[AssistantTurn, bool]:
    """Reserve the turn before the model is called, so a repeat or a second message in flight
    cannot call it twice. False with the stored turn when this one was already answered."""
    now = timezone.now()
    with transaction.atomic():
        locked = AssistantSession.objects.select_for_update().filter(pk=session.pk).first()
        if locked is None:
            raise DomainError("not_found")
        if locked.closed_at is not None:
            raise DomainError("conflict")
        turns = AssistantTurn.objects.filter(session=locked)
        turns.filter(reply_text="", created_at__lt=now - ABANDONED_AFTER).delete()
        same = turns.filter(client_turn_id=client_turn_id).first()
        if same is not None and same.reply_text:
            if same.user_text != text:
                raise DomainError("idempotency_key_reused")
            return same, False
        if turns.filter(reply_text="").exists() or turns.count() >= MAX_TURNS:
            raise DomainError("conflict")
        last = turns.aggregate(last=Max("sequence"))["last"] or 0
        row = AssistantTurn.objects.create(
            session=locked,
            sequence=last + 1,
            client_turn_id=client_turn_id,
            user_text=text,
            created_at=now,
        )
    return row, True


def _limit_reached(actor: Actor) -> bool:
    """The gateway enforces the limit; this only avoids spending an embedding on a refused turn."""
    key = ratelimit.derive_key("account", str(actor.account_id))
    return ratelimit.peek(gateway.ASSISTANT_TURN.account_scope, key)


def _knowledge(actor: Actor, text: str) -> catalog.KnowledgeResult:
    if not _QUESTION.search(text) or _limit_reached(actor):
        return catalog.KnowledgeResult("not_found")
    # placeholders can push redacted text past the cap
    return catalog.retrieve_knowledge(actor, text[: catalog.KNOWLEDGE_MAX_QUESTION])


def _request(
    values: KnownValues, turn: AssistantTurn, safe: RedactedText, passages, listing
) -> LLMRequest:
    earlier = list(
        AssistantTurn.objects.filter(session_id=turn.session_id, sequence__lt=turn.sequence)
        .exclude(reply_text="")
        .order_by("-sequence")[:HISTORY_TURNS]
    )
    history = tuple(
        Message(role, redact(text, values))  # again: the account's values may have changed
        for row in reversed(earlier)
        for role, text in (("user", row.user_text), ("assistant", row.reply_text))
    )
    asked = planner.next_step(listing)
    if asked is not None:  # our own words (field names only), so "five" has a question
        history += (Message("assistant", redact(asked.question, KnownValues())),)
    context = tuple(
        redact(f"{p.title} (source: {p.source}, version {p.version})\n{p.text}", KnownValues())
        for p in passages
    )
    return LLMRequest(
        system=redact(prompts.SYSTEM, KnownValues()),
        input=safe,
        history=history,
        context=context,
        schema=extracting.turn_schema(listing.fields["purpose"], listing.fields["subtype"]),
    )


def _clean_reply(text: str, values: KnownValues) -> str:
    """Model output is untrusted: plain characters only, redacted, bounded."""
    plain = _CONTROL.sub("", text[: MAX_REPLY * 2].replace("\r\n", "\n"))
    return redact(plain, values).value[:MAX_REPLY].strip()


def send_turn(actor: Actor, session_id, message, client_turn_id) -> TurnResult:
    """One turn. Refusals, in order: `unauthenticated`, `not_found` (not the caller's session),
    `forbidden` (no `edit_drafts`), `conflict` (not a Draft any more), invalid values, then
    `conflict` while another turn is in flight or the session is full, `idempotency_key_reused`
    for a repeated `client_turn_id` with another message, and `throttled` over the message limit.

    A repeat of an answered turn returns it without calling the model. The extracted fields are
    written against the revision read BEFORE the model call, in the same transaction as the turn."""
    session = owned_session(actor, session_id)
    listing = listings.require_draft(actor, session.listing_id)
    text, key = _check(message, client_turn_id)
    values = known_values_for(actor)
    safe = redact(text, values)
    turn, is_new = _claim(session, key, safe.value)
    if not is_new:
        return TurnResult(
            "success", "", turn_view(turn), planner.next_step(listing), facts_of(listing)
        )
    pending = AssistantTurn.objects.filter(pk=turn.pk, reply_text="")
    answered = False
    try:
        knowledge = _knowledge(actor, safe.value)
        request = _request(values, turn, safe, knowledge.passages, listing)
        result = gateway.complete(actor, gateway.ASSISTANT_TURN, request)
        if not result.ok:
            if result.outcome == "blocked":
                raise ratelimit.RateLimited(result.retry_after)
            return TurnResult(result.outcome, result.message, None)
        parsed = extracting.parse(result.value.text)
        if parsed is None:
            return TurnResult("error", gateway.MESSAGES["error"], None)
        reply = _clean_reply(parsed.reply, values) or (
            "Noted." if parsed.fields or parsed.nearby else ""
        )
        if not reply:
            return TurnResult("error", gateway.MESSAGES["error"], None)
        turn.reply_text = reply
        turn.sources = [
            {"title": p.title, "source": p.source, "version": p.version} for p in knowledge.passages
        ]
        with transaction.atomic():  # the Draft changes only if the turn is kept
            nearby = extracting.stated_nearby(parsed.nearby, safe.value)
            written = extracting.apply(actor, listing, parsed.fields, values, nearby)
            notices = []
            if safe.value != text:
                notices.append(extracting.CONTACT_NOTICE)
            if parsed.clarify:
                notices.append(_clean_reply(parsed.clarify, values))
            notices.append(extracting.unmatched_notice(parsed.unmatched, safe.value))
            turn.result = written.as_json([n for n in notices if n])
            if not pending.update(
                reply_text=turn.reply_text, sources=turn.sources, result=turn.result
            ):
                raise DomainError("not_found")  # the Draft was deleted meanwhile
        answered = True
    finally:
        if not answered:
            pending.delete()
    notice = "" if knowledge.ok else knowledge.message
    after = listings.require_draft(actor, session.listing_id)
    return TurnResult("success", notice, turn_view(turn), planner.next_step(after), facts_of(after))


DESCRIPTION_FAILED = (
    "I could not write a description from your confirmed details. Confirm more details or write "
    "it yourself."
)
_FIXED = frozenset({"purpose", "category", "subtype"})  # chosen when the Draft starts


def _is_manual(listing: ListingView) -> bool:
    if not listing.fields.get("description"):
        return False
    return listing.provenance.get("description", {}).get("origin") != Origin.AI.value


def write_description(actor: Actor, session_id, base_revision, replace=False) -> DescriptionResult:
    """Write a description from the confirmed details and save it as an unconfirmed AI value
    (Story 5.13), with one model call. Refusals as for a turn, then invalid input, a stale
    `base_revision` (also when the Draft changes while the model works), a description of the
    Seller's own without `replace`, nothing confirmed to write from, and the message limit."""
    session = owned_session(actor, session_id)
    listing = listings.require_draft(actor, session.listing_id)
    if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
        raise listings.FieldsInvalid({"base_revision": "invalid"})
    if not isinstance(replace, bool):
        raise listings.FieldsInvalid({"replace": "invalid"})
    if listing.revision != base_revision:
        raise listings.StaleRevision(listing)
    if _is_manual(listing) and not replace:
        raise listings.FieldsInvalid({"description": "manual_description"})
    values = known_values_for(actor)
    facts, lines, left_out = [], [], []
    for fact in facts_of(listing):
        if not fact.confirmed:
            left_out.append(fact.field)
            continue
        line = redact(f"{fact.field}: {fact.value}", values)
        if line.value == f"{fact.field}: {fact.value}":  # never a value redaction would change
            facts.append(fact)
            lines.append(line)
    if not {f.field for f in facts} - _FIXED:
        raise listings.FieldsInvalid({"description": "nothing_confirmed"})
    facts = tuple(facts)
    request = LLMRequest(
        system=redact(describing.SYSTEM, KnownValues()),
        input=redact("Write the description.", KnownValues()),
        context=tuple(lines),
        schema=describing.schema(facts),
    )
    result = gateway.complete(actor, gateway.DESCRIPTION, request)
    if not result.ok:
        if result.outcome == "blocked":
            raise ratelimit.RateLimited(result.retry_after)
        return DescriptionResult(result.outcome, result.message)
    answer = describing.read(result.value.text, facts, values)
    if answer is None:
        return DescriptionResult("error", DESCRIPTION_FAILED, left_out=tuple(left_out))
    text, claims = answer
    try:
        view = listings.apply_updates(
            actor, listing.id, listing.revision, {"description": text}, Origin.AI
        )
    except listings.FieldsInvalid:
        return DescriptionResult("error", DESCRIPTION_FAILED, left_out=tuple(left_out))
    return DescriptionResult("success", "", view, claims, tuple(left_out))
