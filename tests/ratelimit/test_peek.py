from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.kernel import ratelimit
from apps.kernel.models import RateLimitCounter

T0 = datetime(2026, 1, 1, 12, 0, 10, tzinfo=UTC)


def test_peek_never_counts(probe):
    assert ratelimit.peek("probe", "k", T0) is False
    assert ratelimit.peek("probe", "k", T0) is False
    assert RateLimitCounter.objects.count() == 0


def test_peek_reports_reached_at_the_limit_not_before(probe):
    for n in range(1, 4):
        ratelimit.hit("probe", "k", T0)
        assert ratelimit.peek("probe", "k", T0) is (n >= 3)
    assert RateLimitCounter.objects.get().count == 3  # peeks added nothing


def test_peek_is_per_key_and_per_window(probe):
    for _ in range(3):
        ratelimit.hit("probe", "k", T0)
    assert ratelimit.peek("probe", "other", T0) is False
    assert ratelimit.peek("probe", "k", T0 + timedelta(seconds=50)) is False


def test_peek_of_a_cap_or_unknown_scope_is_a_configuration_error(db, seeded):
    with pytest.raises(ImproperlyConfigured):
        ratelimit.peek("favourites", "k", T0)
    with pytest.raises(ImproperlyConfigured):
        ratelimit.peek("no_such_scope", "k", T0)


def test_peek_needs_an_aware_time(probe):
    with pytest.raises(ValueError):
        ratelimit.peek("probe", "k", datetime(2026, 1, 1))


def test_peek_fails_open_and_logs_class_only(probe, monkeypatch, caplog):
    monkeypatch.setattr(ratelimit, "_PEEK", "SELECT 1 FROM table_that_does_not_exist")
    with caplog.at_level("WARNING", logger="estate.ratelimit"):
        assert ratelimit.peek("probe", "k", T0) is False
    assert caplog.records[-1].exception_class == "ProgrammingError"
    assert ratelimit.limit_for("probe").limit == 3
