import io
import logging
import uuid
from datetime import datetime, timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.kernel import ai_log, idempotency, ratelimit, retention
from apps.kernel.management.commands.run_retention import Command
from apps.kernel.models import AiRequestLog, IdempotencyRecord, RateLimitCounter

A = uuid.UUID("11111111-1111-4111-8111-111111111111")
H = idempotency.request_fingerprint({"a": 1})


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(retention, "_jobs", {})


def run(*args):
    out = io.StringIO()
    try:
        call_command("run_retention", *args, stdout=out)
        error = None
    except CommandError as exc:
        error = exc
    return out.getvalue().splitlines(), error


def test_kernel_jobs_are_registered_in_order():
    assert retention.registered() == [
        "kernel.idempotency",
        "kernel.ratelimit",
        "kernel.ai_request_log",
        "kernel.notifications",
        "kernel.media_orphans",
        "identity.email_verification_tokens",
        "identity.password_reset_tokens",
        "identity.email_change_tokens",
        "identity.sessions",
        "listings.draft_notice",
        "listings.draft_expiry",
    ]


def test_registration_rules(isolated):
    retention.register("app.one", lambda now, dry_run: 0)
    retention.register("app.two", lambda now, dry_run: 0)
    assert retention.registered() == ["app.one", "app.two"]
    with pytest.raises(ValueError):
        retention.register("app.one", lambda now, dry_run: 0)
    with pytest.raises(ValueError):
        retention.register("Bad Name", lambda now, dry_run: 0)
    with pytest.raises(ValueError):
        retention.register("app.three", "not callable")


def seed_old_and_new(now):
    old = now - timedelta(days=8)
    for key, created in (("old", old), ("new", now - timedelta(days=1))):
        IdempotencyRecord.objects.create(
            scope="probe", account_id=A, key=key, request_hash=H, created_at=created
        )
    ai_log.record_ai_call("probe", "groq", "m1", 1, "success", now=now - timedelta(days=91))
    ai_log.record_ai_call("probe", "groq", "m1", 1, "success", now=now - timedelta(days=1))
    ended = now - timedelta(hours=3)
    RateLimitCounter.objects.create(scope="registration", key_hash="k", window_start=ended, count=1)
    RateLimitCounter.objects.create(scope="registration", key_hash="k2", window_start=now, count=1)


def test_dry_run_reports_counts_and_deletes_nothing(db):
    seed_old_and_new(timezone.now())
    lines, error = run("--dry-run")
    assert error is None
    assert lines == [
        "kernel.idempotency: would delete 1",
        "kernel.ratelimit: would delete 1",
        "kernel.ai_request_log: would delete 1",
        "kernel.notifications: would delete 0",
        "kernel.media_orphans: would delete 0",
        "identity.email_verification_tokens: would delete 0",
        "identity.password_reset_tokens: would delete 0",
        "identity.email_change_tokens: would delete 0",
        "identity.sessions: would delete 0",
        "listings.draft_notice: would delete 0",
        "listings.draft_expiry: would delete 0",
    ]
    assert IdempotencyRecord.objects.count() == 2
    assert AiRequestLog.objects.count() == 2
    assert RateLimitCounter.objects.count() == 2


def test_real_run_deletes_expired_and_keeps_newer(db):
    seed_old_and_new(timezone.now())
    lines, error = run()
    assert error is None
    assert lines == [
        "kernel.idempotency: deleted 1",
        "kernel.ratelimit: deleted 1",
        "kernel.ai_request_log: deleted 1",
        "kernel.notifications: deleted 0",
        "kernel.media_orphans: deleted 0",
        "identity.email_verification_tokens: deleted 0",
        "identity.password_reset_tokens: deleted 0",
        "identity.email_change_tokens: deleted 0",
        "identity.sessions: deleted 0",
        "listings.draft_notice: deleted 0",
        "listings.draft_expiry: deleted 0",
    ]
    assert list(IdempotencyRecord.objects.values_list("key", flat=True)) == ["new"]
    assert AiRequestLog.objects.count() == 1
    assert list(RateLimitCounter.objects.values_list("key_hash", flat=True)) == ["k2"]


def test_purge_functions_dry_run_matches_real(db):
    now = timezone.now()
    seed_old_and_new(now)
    assert idempotency.purge_expired(now, dry_run=True) == idempotency.purge_expired(now) == 1
    assert ratelimit.purge_expired(now, dry_run=True) == ratelimit.purge_expired(now) == 1


def test_failing_job_is_isolated_and_exit_is_non_zero(db, isolated, caplog):
    calls = []

    def good(now, dry_run):
        calls.append("good")
        return 3

    def bad(now, dry_run):
        raise RuntimeError("secret detail")

    retention.register("app.first", good)
    retention.register("app.bad", bad)
    retention.register("app.last", good)
    with caplog.at_level(logging.ERROR, logger="estate.retention"):
        lines, error = run()
    assert calls == ["good", "good"]
    assert str(error) == "1 retention job(s) failed"
    with pytest.raises(SystemExit) as exit_info:
        Command().run_from_argv(["manage.py", "run_retention"])
    assert exit_info.value.code == 1
    assert lines == [
        "app.first: deleted 3",
        "app.bad: FAILED (RuntimeError)",
        "app.last: deleted 3",
    ]
    record = next(r for r in caplog.records if r.name == "estate.retention")
    assert record.handler == "app.bad" and record.exception_class == "RuntimeError"
    assert "secret detail" not in caplog.text


def test_each_job_has_its_own_transaction(db, isolated):
    def writes_then_fails(now, dry_run):
        IdempotencyRecord.objects.create(
            scope="probe", account_id=A, key="rolled", request_hash=H, created_at=now
        )
        raise RuntimeError

    def writes(now, dry_run):
        IdempotencyRecord.objects.create(
            scope="probe", account_id=A, key="kept", request_hash=H, created_at=now
        )
        return 1

    retention.register("app.fails", writes_then_fails)
    retention.register("app.ok", writes)
    run()
    assert list(IdempotencyRecord.objects.values_list("key", flat=True)) == ["kept"]


def test_run_all_requires_aware_now():
    with pytest.raises(ValueError, match="timezone-aware"):
        retention.run_all(datetime(2026, 1, 1))


@pytest.mark.parametrize("bad", [None, "3", 1.5, True])
def test_non_int_result_is_a_failed_job(db, isolated, bad):
    retention.register("app.bad", lambda now, dry_run: bad)
    results = retention.run_all(timezone.now())
    assert results == [retention.JobResult("app.bad", None, "TypeError")]


def test_same_callable_registers_idempotently_but_a_different_one_raises(isolated):
    def job(now, dry_run):
        return 0

    retention.register("app.one", job)
    retention.register("app.one", job)
    assert retention.registered() == ["app.one"]
    with pytest.raises(ValueError):
        retention.register("app.one", lambda now, dry_run: 0)


def test_kernel_ready_twice_does_not_crash():
    from django.apps import apps

    apps.get_app_config("kernel").ready()
    assert retention.registered()[:3] == [
        "kernel.idempotency",
        "kernel.ratelimit",
        "kernel.ai_request_log",
    ]


def test_empty_job_list_says_so(isolated):
    lines, error = run()
    assert error is None and lines == ["no retention jobs registered"]
