import threading
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection

from apps.kernel import ratelimit
from apps.kernel.models import RateLimitCounter, RateLimitPolicy

T0 = datetime(2026, 1, 1, 12, 0, 10, tzinfo=UTC)  # 10 s into a 60 s window


def test_under_then_over_limit_with_retry_after(probe):
    for n in range(1, 4):
        decision = ratelimit.hit("probe", "k", T0)
        assert decision.allowed and decision.count == n
    over = ratelimit.hit("probe", "k", T0)
    assert not over.allowed
    assert over.retry_after == 50


def test_new_window_restarts_the_count(probe):
    for _ in range(4):
        ratelimit.hit("probe", "k", T0)
    later = ratelimit.hit("probe", "k", T0 + timedelta(seconds=50))
    assert later.allowed and later.count == 1


def test_enforce_raises_with_retry_after(probe):
    for _ in range(3):
        ratelimit.enforce("probe", "k", T0)
    with pytest.raises(ratelimit.RateLimited) as caught:
        ratelimit.enforce("probe", "k", T0)
    assert caught.value.retry_after == 50
    assert caught.value.code == "throttled"


def test_keys_have_separate_counters(probe):
    for _ in range(4):
        ratelimit.hit("probe", "a", T0)
    assert ratelimit.hit("probe", "b", T0).allowed


def test_store_failure_fails_open_and_logs_class_only(probe, monkeypatch, caplog):
    monkeypatch.setattr(ratelimit, "_UPSERT", "SELECT 1 FROM table_that_does_not_exist")
    with caplog.at_level("WARNING", logger="estate.ratelimit"):
        decision = ratelimit.hit("probe", "k", T0)
    assert decision.allowed
    record = caplog.records[-1]
    assert record.exception_class == "ProgrammingError"
    assert ratelimit.limit_for("probe").limit == 3  # the transaction is still usable
    assert "table_that_does_not_exist" not in caplog.text


def test_purge_removes_only_ended_windows(probe):
    ratelimit.hit("probe", "old", T0)
    ratelimit.hit("probe", "new", T0 + timedelta(seconds=120))
    # at T0+90 s the first window (ends T0+50 s) is over, the second is not started
    assert ratelimit.purge_expired(T0 + timedelta(seconds=90)) == 1
    assert list(RateLimitCounter.objects.values_list("key_hash", flat=True)) == ["new"]


def test_purge_drops_counters_of_removed_scopes(probe):
    ratelimit.hit("probe", "k", T0)
    RateLimitPolicy.objects.filter(scope="probe").delete()
    assert ratelimit.purge_expired(T0) == 1


# Teardown truncates the seed rows; seed-dependent tests use the `seeded` fixture.
@pytest.mark.django_db(transaction=True)
def test_concurrent_hits_allow_exactly_the_limit():
    RateLimitPolicy.objects.create(
        scope="burst", limit=7, window_seconds=3600, key_kind="account", note="test"
    )
    results, errors = [], []
    barrier = threading.Barrier(30)
    start = datetime.now(UTC)

    def worker():
        try:
            barrier.wait()
            results.append(ratelimit.hit("burst", "same", start).allowed)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert results.count(True) == 7 and results.count(False) == 23
    assert RateLimitCounter.objects.get(scope="burst").count == 8


def test_stored_count_is_capped_at_limit_plus_one(probe):
    for _ in range(10):
        ratelimit.hit("probe", "k", T0)
    assert RateLimitCounter.objects.get(key_hash="k").count == 4
    assert ratelimit.hit("probe", "k", T0).count == 4
