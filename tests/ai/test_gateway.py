import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError

from apps.kernel import flags, runtime
from apps.kernel.actor import Actor
from apps.kernel.ai import adapters, budget, gateway
from apps.kernel.ai.ports import Audio, LLMRequest
from apps.kernel.ai.redaction import KnownValues, redact
from apps.kernel.errors import DomainError
from apps.kernel.models import AiBudgetLedger, AiRequestLog

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
SYSTEM = Actor.system("r")


def safe(text="hello"):
    return redact(text, KnownValues())


def request(text="hello"):
    return LLMRequest(system=safe("Be brief."), input=safe(text))


def someone():
    return Actor.user(uuid.uuid4(), "r")


def turn(actor, text="hello", now=NOW):
    return gateway.complete(actor, gateway.ASSISTANT_TURN, request(text), now=now)


def search(actor, text="house", **kwargs):
    kwargs.setdefault("now", NOW)
    return gateway.complete(actor, gateway.SEARCH_QUERY, request(text), **kwargs)


def logged():
    return list(AiRequestLog.objects.values_list("feature", "provider", "model", "outcome"))


def test_the_stub_is_the_default_and_the_call_is_logged_without_content(recorded_stub):
    result = turn(someone(), "a private sentence")
    assert result.ok and result.value.text == recorded_stub.reply
    assert adapters.get_adapter() is recorded_stub
    assert recorded_stub.requests[0].texts == ("Be brief.", "a private sentence")
    assert logged() == [("assistant_turn", "stub", "recorded-stub", "success")]
    assert "sentence" not in str(list(AiRequestLog.objects.values()))


def test_an_unknown_provider_is_refused(settings):
    settings.AI_PROVIDER = "nobody"
    with pytest.raises(ImproperlyConfigured):
        adapters.get_adapter()


@pytest.mark.parametrize(
    ("feature", "limit"), [(gateway.ASSISTANT_TURN, 2000), (gateway.SEARCH_QUERY, 500)]
)
def test_input_caps_count_the_text_as_typed(recorded_stub, feature, limit):
    assert gateway.complete(someone(), feature, request("a" * limit), now=NOW).ok
    with pytest.raises(gateway.InputTooLong) as raised:
        gateway.complete(someone(), feature, request("a" * (limit + 1)), now=NOW)
    assert raised.value.code == "validation_error"
    assert len(recorded_stub.requests) == 1
    assert sorted(row[3] for row in logged()) == ["blocked", "success"]


def test_timeouts_are_the_documented_ones():
    assert (gateway.SEARCH_QUERY.timeout, gateway.ASSISTANT_TURN.timeout) == (12.0, 20.0)


def test_a_visitor_cannot_take_an_assistant_turn(recorded_stub):
    with pytest.raises(DomainError):
        turn(Actor.visitor())
    assert not recorded_stub.requests


def test_thirty_assistant_messages_an_hour_per_account(recorded_stub):
    actor = someone()
    assert all(turn(actor).ok for _ in range(30))
    refused = turn(actor)
    assert (refused.outcome, refused.value) == ("blocked", None)
    assert refused.message and refused.retry_after > 0
    assert len(recorded_stub.requests) == 30
    assert turn(someone()).ok
    assert turn(actor, now=NOW + timedelta(hours=1)).ok


def test_visitor_searches_are_limited_by_client_key(recorded_stub):
    visitor = Actor.visitor()
    assert all(search(visitor, visitor_key="k1").ok for _ in range(10))
    assert search(visitor, visitor_key="k1").outcome == "blocked"
    assert search(visitor, visitor_key="k2").ok
    with pytest.raises(ValueError):
        search(visitor)


def test_a_flag_off_falls_back_at_once(recorded_stub):
    flags.set_flag(SYSTEM, "ai_gateway", False)
    actor = someone()
    results = [
        turn(actor),
        gateway.embed(actor, [safe()]),
        gateway.transcribe(actor, Audio(b"x", "audio/webm"), now=NOW),
    ]
    assert {r.outcome for r in results} == {"fallback"} and all(r.message for r in results)
    assert not recorded_stub.requests and not AiBudgetLedger.objects.exists()
    assert [row[3] for row in logged()] == ["fallback"] * 3


def test_a_provider_error_body_is_never_logged_or_returned(recorded_stub, caplog):
    recorded_stub.fail_with = RuntimeError("provider echoed: secret-words-here")
    with caplog.at_level(logging.DEBUG):
        result = turn(someone())
    assert result.outcome == "error" and result.value is None
    assert "secret-words" not in result.message + repr(result)
    assert "secret-words" not in "".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert logged()[0][3] == "error"


def test_a_slow_call_times_out(recorded_stub):
    recorded_stub.delay = 0.5
    feature = gateway.Feature("slow", timeout=0.05)
    result = gateway.complete(someone(), feature, request(), now=NOW)
    assert result.outcome == "timeout" and logged()[0][3] == "timeout"


def test_a_busy_executor_falls_back(recorded_stub, settings):
    release = threading.Event()
    runtime.shutdown()
    settings.RUNTIME_EXECUTOR_MAX_WORKERS = 2
    settings.RUNTIME_EXECUTOR_RESERVED_WORKERS = 1
    holder = runtime.submit_heavy(release.wait, 5)
    try:
        assert turn(someone()).outcome == "fallback"
    finally:
        release.set()
        holder.result(timeout=5)


def test_a_logging_fault_does_not_fail_the_call(monkeypatch):
    def broken(*args, **kwargs):
        raise DatabaseError

    monkeypatch.setattr(gateway.ai_log, "record_ai_call", broken)
    assert turn(someone()).ok


def test_at_least_half_of_the_budget_is_kept_for_listing_creation(recorded_stub, settings):
    settings.AI_DAILY_BUDGET = 5
    assert [search(someone()).outcome for _ in range(3)] == [
        "success",
        "success",
        "budget_exhausted",
    ]
    assert [turn(someone()).outcome for _ in range(4)] == [
        "success",
        "success",
        "success",
        "budget_exhausted",
    ]
    assert len(recorded_stub.requests) == 5
    row = AiBudgetLedger.objects.get(provider="stub")
    assert (row.used, row.open_used) == (5, 2)
    assert "capacity" in turn(someone()).message
    assert turn(someone(), now=NOW + timedelta(days=1)).ok


def test_listing_creation_may_use_the_whole_budget(settings):
    settings.AI_DAILY_BUDGET = 4
    assert all(turn(someone()).ok for _ in range(4))
    assert search(someone()).outcome == "budget_exhausted"


def test_embeddings_use_no_budget():
    assert gateway.embed(someone(), [safe()]).ok
    assert not AiBudgetLedger.objects.exists()


def test_a_budget_store_fault_fails_closed(monkeypatch, recorded_stub):
    def broken(*args, **kwargs):
        raise DatabaseError

    monkeypatch.setattr(budget, "take", broken)
    assert turn(someone()).outcome == "fallback"
    assert not recorded_stub.requests


@pytest.mark.parametrize(("daily", "percent"), [(10, 49), (10, 101), (-1, 50)])
def test_budget_settings_are_validated(daily, percent):
    with pytest.raises(ImproperlyConfigured):
        budget.validate_limits(daily, percent)


def test_old_ledger_rows_are_purged():
    budget.take("stub", reserved=True, now=NOW - timedelta(days=100))
    budget.take("stub", reserved=True, now=NOW)
    assert budget.purge_expired(NOW, dry_run=True) == 1
    assert budget.purge_expired(NOW) == 1
    assert AiBudgetLedger.objects.count() == 1


def test_oversized_audio_is_refused(recorded_stub):
    audio = Audio(b"x" * (gateway.MAX_AUDIO_BYTES + 1), "audio/webm")
    with pytest.raises(gateway.InputTooLong):
        gateway.transcribe(someone(), audio, now=NOW)
    assert not recorded_stub.requests
