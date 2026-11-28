"""Natural-language search (Stories 6.1 and 6.2): one model call turns a request into the query
parameters of the public list. Nothing is searched here: the client reads the results from
`GET /api/v1/public/listings`, whose only query is `listings.selectors.search_public`, and editing
the interpretation is that same read, with no AI.

Only redacted text reaches the model, and neither the request nor the answer is stored or logged.
Whatever goes wrong, the answer is a fallback that sends the buyer to the conventional filters;
only the rate limit is an error (429).

Voice (Story 6.3) is a step before that: the recording becomes a transcript the buyer reads, edits
and then searches with `interpret_query`. The audio is held in memory for the one call: it is
never stored or logged, and goes to the provider with no account data.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from apps.kernel import ratelimit
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway
from apps.kernel.ai.known_values import known_values_for
from apps.kernel.ai.ports import Audio, LLMRequest
from apps.kernel.ai.redaction import KnownValues, redact
from apps.search import audio, interpretation

logger = logging.getLogger("estate.search")

MAX_QUERY = gateway.SEARCH_QUERY.max_chars
USE_FILTERS = "Use the filters to search instead."
UNREADABLE = f"We could not turn that into a search. {USE_FILTERS}"
NOTHING_FOUND = f"We found no search criteria in that. {USE_FILTERS}"
_FAILED = {
    "timeout": f"Search by description took too long. {USE_FILTERS}",
    "budget_exhausted": f"Today's AI capacity is used up. {USE_FILTERS}",
}
UNAVAILABLE = f"Search by description is unavailable right now. {USE_FILTERS}"

TYPE_INSTEAD = "Type your search instead."
_VOICE_FAILED = {
    "timeout": f"Voice search took too long. {TYPE_INSTEAD}",
    "budget_exhausted": f"Today's AI capacity is used up. {TYPE_INSTEAD}",
}
VOICE_UNAVAILABLE = f"Voice search is unavailable right now. {TYPE_INSTEAD}"
NOT_HEARD = f"We did not hear a search in that. {TYPE_INSTEAD}"


@dataclass(frozen=True)
class SearchResult:
    interpreted: bool
    message: str = ""  # why not, when not interpreted
    read: interpretation.Interpretation = interpretation.Interpretation()

    @property
    def ok(self) -> bool:
        return self.interpreted


def request_for(actor: Actor, query: str) -> LLMRequest:
    return LLMRequest(
        system=redact(interpretation.PROMPT, KnownValues()),
        input=redact(query, known_values_for(actor)),
        schema=interpretation.schema(),
    )


def interpret_query(
    actor: Actor, query: str, *, visitor_key: str | None = None, now: datetime | None = None
) -> SearchResult:
    """`query` is at most `MAX_QUERY` characters (`gateway.InputTooLong` otherwise). A Visitor
    needs `visitor_key`. Raises `ratelimit.RateLimited` over the search limit."""
    if len(query) > MAX_QUERY:  # before redaction, which has a limit of its own
        raise gateway.InputTooLong
    request = request_for(actor, query)
    result = gateway.complete(
        actor, gateway.SEARCH_QUERY, request, visitor_key=visitor_key, now=now
    )
    if result.outcome == "blocked":
        raise ratelimit.RateLimited(result.retry_after)
    if not result.ok:
        return SearchResult(False, _FAILED.get(result.outcome, UNAVAILABLE))
    try:
        read = interpretation.interpret(result.value.text, request.input.value)
    except Exception as exc:  # untrusted input: never a failed search, and only the class
        logger.warning(
            "search_interpretation_failure",
            extra={
                "event": "search_interpretation_failure",
                "exception_class": type(exc).__name__,
            },
        )
        return SearchResult(False, UNREADABLE)
    # the share of rejected and repaired answers (SM-3) is counted from these events
    logger.info("search_interpretation", extra={"event": f"search_interpretation_{read.quality}"})
    if read.quality == interpretation.REJECTED:
        return SearchResult(False, UNREADABLE, read)
    if read.empty:
        return SearchResult(False, NOTHING_FOUND, read)
    return SearchResult(True, read=read)


# The provider marks sounds that are not words, as in "<noise>" or "[inaudible]".
_SOUND_TAG = re.compile(r"<[A-Za-z _/-]{1,30}>|\[[A-Za-z _-]{1,30}\]")


@dataclass(frozen=True)
class Transcription:
    transcript: str = ""
    message: str = ""  # why there is no transcript

    @property
    def ok(self) -> bool:
        return bool(self.transcript)


class AudioRejected(Exception):
    """`reason` is a field error code; nothing was sent or counted."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def transcribe_query(
    actor: Actor, data: bytes, *, visitor_key: str | None = None, now: datetime | None = None
) -> Transcription:
    """The words of a recorded search. A Visitor needs `visitor_key`. Raises `AudioRejected`
    for a recording that is empty, too large or not an accepted type, and
    `ratelimit.RateLimited` over the voice limit."""
    if not data:
        raise AudioRejected("empty")
    if len(data) > gateway.MAX_AUDIO_BYTES:
        raise AudioRejected("audio_too_large")
    kind = audio.content_type(data)
    if kind is None:
        raise AudioRejected("audio_type")
    result = gateway.transcribe(actor, Audio(data, kind), visitor_key=visitor_key, now=now)
    if result.outcome == "blocked":
        raise ratelimit.RateLimited(result.retry_after)
    if not result.ok:
        return Transcription(message=_VOICE_FAILED.get(result.outcome, VOICE_UNAVAILABLE))
    # one line, no sound tags or control characters, and no longer than a search may be
    heard = _SOUND_TAG.sub(" ", str(result.value))
    heard = "".join(c if c.isprintable() else " " for c in heard)
    heard = " ".join(heard.split())[:MAX_QUERY].strip()
    return Transcription(heard) if heard else Transcription(message=NOT_HEARD)
