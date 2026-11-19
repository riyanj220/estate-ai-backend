from datetime import UTC, datetime, timedelta

import pytest
from django.db import IntegrityError, connection, transaction

from apps.kernel import ai_log
from apps.kernel.models import AiRequestLog

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
GOOD = dict(feature="listing_assistant", provider="groq", model="llama-3.3-70b", latency_ms=420)


def test_valid_record_stores_exactly_the_six_fields(db):
    ai_log.record_ai_call(**GOOD, outcome="success", now=T0)
    row = AiRequestLog.objects.get()
    assert (row.feature, row.provider, row.model, row.latency_ms, row.outcome, row.created_at) == (
        "listing_assistant",
        "groq",
        "llama-3.3-70b",
        420,
        "success",
        T0,
    )
    columns = {f.name for f in AiRequestLog._meta.get_fields()}
    assert columns == {"id", "feature", "provider", "model", "latency_ms", "outcome", "created_at"}


@pytest.mark.parametrize("outcome", sorted(ai_log.OUTCOMES))
def test_every_outcome_is_accepted(db, outcome):
    ai_log.record_ai_call(**GOOD, outcome=outcome)
    assert AiRequestLog.objects.get().outcome == outcome


@pytest.mark.parametrize(
    "override",
    [
        {"feature": "find me a house in DHA phase 5"},
        {"feature": "Listing"},
        {"feature": "x" * 49},
        {"feature": ""},
        {"provider": "Groq"},
        {"provider": "a b"},
        {"model": "user@example.com"},
        {"model": "m" * 49},
        {"outcome": "ok"},
        {"outcome": "SUCCESS"},
        {"latency_ms": -1},
        {"latency_ms": 1.5},
        {"latency_ms": True},
        {"feature": None},
    ],
)
def test_invalid_input_raises_and_stores_nothing(db, override):
    args = {**GOOD, "outcome": "success", **override}
    with pytest.raises(ValueError):
        ai_log.record_ai_call(**args)
    assert not AiRequestLog.objects.exists()


def test_database_refuses_an_unknown_outcome(db):
    with pytest.raises(IntegrityError), transaction.atomic():
        AiRequestLog.objects.create(**GOOD, outcome="nope", created_at=T0)


def test_purge_keeps_rows_inside_90_days(db):
    now = T0 + timedelta(days=200)
    for age in (91, 90, 10):
        ai_log.record_ai_call(**GOOD, outcome="success", now=now - timedelta(days=age))
    assert ai_log.purge_expired(now, dry_run=True) == 1
    assert AiRequestLog.objects.count() == 3
    assert ai_log.purge_expired(now) == 1
    assert AiRequestLog.objects.count() == 2
    with pytest.raises(ValueError):
        ai_log.purge_expired(datetime(2026, 1, 1))


def test_created_at_is_indexed(db):
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'kernel_airequestlog'")
        assert "ai_request_log_created_idx" in {r[0] for r in cursor.fetchall()}


def test_normalise_token_makes_real_ids_acceptable(db):
    assert ai_log.normalise_token("Meta-Llama/Llama-3.3:70B") == "meta-llama-llama-3.3-70b"
    assert ai_log.normalise_token("/Gemini 2.5") == "gemini-2.5"
    assert len(ai_log.normalise_token("A" * 100)) == 48
    ai_log.record_ai_call(
        "listing_assistant",
        ai_log.normalise_token("Groq"),
        ai_log.normalise_token("openai/GPT-OSS:120B"),
        1,
        "success",
    )


def test_latency_overflow_and_naive_now_are_rejected(db):
    with pytest.raises(ValueError):
        ai_log.record_ai_call(**{**GOOD, "latency_ms": 2147483648}, outcome="success")
    ai_log.record_ai_call(**{**GOOD, "latency_ms": 2147483647}, outcome="success")
    with pytest.raises(ValueError, match="timezone-aware"):
        ai_log.record_ai_call(**GOOD, outcome="success", now=datetime(2026, 1, 1))
    assert AiRequestLog.objects.count() == 1
