"""Every AI call goes through here: input cap, feature flag, per-account limit, daily budget,
timeout on the runtime executor, request log (AD-8, AD-25).

A call that cannot be served returns an `AiResult` fallback with a message to show; the caller
offers the manual form or the conventional filters. Only misuse raises: text that is not
`RedactedText` (TypeError), input over the cap (`InputTooLong`), a Visitor on a signed-in feature.
Nothing a provider said in an error is logged or returned: only the exception class is.

Calls block the calling thread up to the timeout, and use one heavy executor slot, so do not call
from a task that already runs on the executor.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from django.db import DatabaseError, transaction

from apps.kernel import ai_log, flags, ratelimit, runtime
from apps.kernel.actor import Actor, require_signed_in
from apps.kernel.ai import budget
from apps.kernel.ai.adapters import get_adapter
from apps.kernel.ai.ports import Audio, LLMRequest, require_redacted
from apps.kernel.errors import DomainError

logger = logging.getLogger("estate.ai")

FLAG = "ai_gateway"
MAX_AUDIO_BYTES = 5 * 1024 * 1024
EMBEDDING_TIMEOUT = 12.0


@dataclass(frozen=True)
class Feature:
    name: str
    timeout: float
    max_chars: int | None = None
    signed_in_only: bool = False
    account_scope: str | None = None
    visitor_scope: str | None = None
    reserved: bool = False  # may use the budget kept for signed-in Listing creation


ASSISTANT_TURN = Feature(
    "assistant_turn",
    timeout=20.0,
    max_chars=2000,
    signed_in_only=True,
    account_scope="assistant_messages",
    reserved=True,
)
DESCRIPTION = Feature(  # shares the assistant's hourly limit
    "listing_description",
    timeout=20.0,
    max_chars=2000,
    signed_in_only=True,
    account_scope="assistant_messages",
    reserved=True,
)
SEARCH_QUERY = Feature(
    "search_query",
    timeout=12.0,
    max_chars=500,
    account_scope="ai_search_account",
    visitor_scope="ai_search_visitor",
)
VOICE_SEARCH = Feature(
    "voice_search",
    timeout=12.0,
    account_scope="ai_search_account",
    visitor_scope="ai_search_visitor",
)

MESSAGES = {
    "fallback": "The AI feature is unavailable right now. You can continue without it.",
    "error": "The AI feature is unavailable right now. You can continue without it.",
    "timeout": "The AI service took too long. You can continue without it.",
    "blocked": "You have reached the AI usage limit for now. Continue without it or try later.",
    "budget_exhausted": (
        "Today's AI capacity is used up. Continue without it or try again tomorrow."
    ),
}


@dataclass(frozen=True)
class AiResult:
    outcome: str
    value: Any = field(default=None, repr=False)
    message: str = ""
    retry_after: int = 0
    status: int | None = None  # the provider's HTTP status of a failed call, when there was one

    @property
    def ok(self) -> bool:
        return self.outcome == "success"


class InputTooLong(DomainError):
    def __init__(self) -> None:
        super().__init__("validation_error")


def _log(feature: str, adapter, started: float, outcome: str) -> None:
    try:
        with transaction.atomic():
            ai_log.record_ai_call(
                feature,
                ai_log.normalise_token(adapter.provider),
                ai_log.normalise_token(adapter.model),
                round((time.monotonic() - started) * 1000),
                outcome,
            )
    except Exception as exc:  # logging never fails the call
        logger.warning(
            "ai_log_failed",
            extra={"event": "ai_log_failed", "exception_class": type(exc).__name__},
        )


def _fallback(
    feature: str, adapter, started: float, outcome: str, retry_after=0, status=None
) -> AiResult:
    _log(feature, adapter, started, outcome)
    return AiResult(outcome, message=MESSAGES[outcome], retry_after=retry_after, status=status)


def _call(feature: str, adapter, started: float, timeout: float, fn) -> AiResult:
    try:
        future = runtime.submit_heavy(fn)
        try:
            value = future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            return _fallback(feature, adapter, started, "timeout")
    except runtime.ExecutorBusy:
        return _fallback(feature, adapter, started, "fallback")
    except Exception as exc:  # the class only: a provider's message may echo the request
        logger.warning(
            "ai_call_failed",
            extra={"event": "ai_call_failed", "exception_class": type(exc).__name__},
        )
        # an adapter may give up on its own socket just before the wait above does
        outcome = "timeout" if getattr(exc, "category", None) == "timeout" else "error"
        return _fallback(feature, adapter, started, outcome, status=getattr(exc, "status", None))
    _log(feature, adapter, started, "success")
    return AiResult("success", value)


def _admit(
    actor: Actor,
    feature: Feature,
    adapter,
    started: float,
    visitor_key: str | None,
    now: datetime | None,
) -> AiResult | None:
    """None when the call may go ahead, else the fallback to return."""
    if not flags.is_enabled(FLAG):
        return _fallback(feature.name, adapter, started, "fallback")
    if actor.account_id is not None:
        scope = feature.account_scope
        key = ratelimit.derive_key("account", str(actor.account_id))
    else:
        scope, key = feature.visitor_scope, visitor_key
    if scope is not None:
        if not key:
            raise ValueError("a Visitor call needs visitor_key")
        decision = ratelimit.hit(scope, key, now)
        if not decision.allowed:
            return _fallback(feature.name, adapter, started, "blocked", decision.retry_after)
    reserved = feature.reserved and actor.account_id is not None
    return _spend(feature.name, adapter, started, reserved=reserved, now=now)


def _spend(
    feature: str, adapter, started: float, *, reserved: bool, now: datetime | None
) -> AiResult | None:
    """Count one provider request; None when allowed, else the fallback."""
    try:
        allowed = budget.take(ai_log.normalise_token(adapter.provider), reserved=reserved, now=now)
    except DatabaseError as exc:  # fail closed: an uncounted call could exhaust a free tier
        logger.warning(
            "ai_budget_failure",
            extra={"event": "ai_budget_failure", "exception_class": type(exc).__name__},
        )
        return _fallback(feature, adapter, started, "fallback")
    if not allowed:
        return _fallback(feature, adapter, started, "budget_exhausted")
    return None


def complete(
    actor: Actor,
    feature: Feature,
    request: LLMRequest,
    *,
    visitor_key: str | None = None,
    now: datetime | None = None,
) -> AiResult:
    """`value` is an `LLMResponse`. `visitor_key` is `ratelimit.client_key(..)` for a Visitor."""
    if type(request) is not LLMRequest:
        raise TypeError("complete takes an LLMRequest")
    require_redacted(*request.texts())
    if feature.signed_in_only:
        require_signed_in(actor)
    adapter, started = get_adapter(), time.monotonic()
    if feature.max_chars is not None and request.input.source_length > feature.max_chars:
        _log(feature.name, adapter, started, "blocked")
        raise InputTooLong
    refused = _admit(actor, feature, adapter, started, visitor_key, now)
    if refused:
        return refused
    return _call(
        feature.name,
        adapter,
        started,
        feature.timeout,
        lambda: adapter.complete(request, timeout=feature.timeout),
    )


def transcribe(
    actor: Actor,
    audio: Audio,
    *,
    visitor_key: str | None = None,
    now: datetime | None = None,
) -> AiResult:
    """Voice search. `value` is the raw transcript: redact it before sending or storing it."""
    if type(audio) is not Audio:
        raise TypeError("transcribe takes Audio")
    feature = VOICE_SEARCH
    adapter, started = get_adapter(), time.monotonic()
    if len(audio.data) > MAX_AUDIO_BYTES:
        _log(feature.name, adapter, started, "blocked")
        raise InputTooLong
    refused = _admit(actor, feature, adapter, started, visitor_key, now)
    if refused:
        return refused
    return _call(
        feature.name,
        adapter,
        started,
        feature.timeout,
        lambda: adapter.transcribe(audio, timeout=feature.timeout),
    )


def embedding_model_key() -> str:
    """Names the model that `embed` would use now: vectors of different keys never compare."""
    adapter = get_adapter()
    return f"{adapter.provider}:{getattr(adapter, 'embedding_model', adapter.model)}"


def embed(actor: Actor, texts, *, reserved: bool = False, now: datetime | None = None) -> AiResult:
    """Flagged and logged, with no per-account limit. A local embedding model needs no budget; a
    provider's counts one request against its daily budget, the open part of it unless `reserved`
    (signed-in Listing creation only)."""
    texts = tuple(texts)
    require_redacted(*texts)
    adapter, started = get_adapter(), time.monotonic()
    logged = SimpleNamespace(
        provider=adapter.provider, model=getattr(adapter, "embedding_model", adapter.model)
    )
    if not flags.is_enabled(FLAG):
        return _fallback("embedding", logged, started, "fallback")
    if not adapter.local_embeddings:
        reserved = reserved and actor.account_id is not None
        refused = _spend("embedding", logged, started, reserved=reserved, now=now)
        if refused:
            return refused
    return _call(
        "embedding",
        logged,
        started,
        EMBEDDING_TIMEOUT,
        lambda: adapter.embed(texts, timeout=EMBEDDING_TIMEOUT),
    )
