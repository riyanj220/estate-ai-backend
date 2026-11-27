"""Natural-language search (Stories 6.1 and 6.2): one model call turns a request into the query
parameters of the public list. Nothing is searched here: the client reads the results from
`GET /api/v1/public/listings`, whose only query is `listings.selectors.search_public`, and editing
the interpretation is that same read, with no AI.

Only redacted text reaches the model, and neither the request nor the answer is stored or logged.
Whatever goes wrong, the answer is a fallback that sends the buyer to the conventional filters;
only the rate limit is an error (429).
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from apps.kernel import ratelimit
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway
from apps.kernel.ai.known_values import known_values_for
from apps.kernel.ai.ports import LLMRequest
from apps.kernel.ai.redaction import KnownValues, redact
from apps.search import interpretation

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
