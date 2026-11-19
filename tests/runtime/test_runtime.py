import asyncio
import contextvars
import logging
import threading
import time

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import AsyncClient, override_settings

from apps.kernel import runtime
from apps.kernel.request_logging import JsonFormatter

var = contextvars.ContextVar("probe", default="unset")


def sized(total, reserved):
    return override_settings(
        RUNTIME_EXECUTOR_MAX_WORKERS=total, RUNTIME_EXECUTOR_RESERVED_WORKERS=reserved
    )


def test_submit_heavy_returns_at_once_and_runs_on_a_worker():
    gate = threading.Event()
    started = time.perf_counter()
    future = runtime.submit_heavy(lambda: (gate.wait(5), threading.current_thread().name)[1])
    assert time.perf_counter() - started < 0.2
    assert not future.done()
    gate.set()
    assert future.result(timeout=5).startswith("estate-runtime")


@sized(3, 1)
def test_heavy_full_raises_busy_immediately_and_slot_is_released():
    gate = threading.Event()
    held = [runtime.submit_heavy(gate.wait, 5) for _ in range(2)]
    started = time.perf_counter()
    with pytest.raises(runtime.ExecutorBusy):
        runtime.submit_heavy(lambda: None)
    assert time.perf_counter() - started < 0.2
    gate.set()
    for future in held:
        future.result(timeout=5)
    assert runtime.submit_heavy(lambda: 7).result(timeout=5) == 7


@sized(3, 1)
def test_reserved_worker_stays_free_for_interactive_work():
    gate = threading.Event()
    held = [runtime.submit_heavy(gate.wait, 5) for _ in range(2)]
    started = time.perf_counter()
    assert runtime.submit_interactive(lambda: "chat").result(timeout=1) == "chat"
    assert time.perf_counter() - started < 0.2
    gate.set()
    for future in held:
        future.result(timeout=5)


def test_task_sees_the_callers_context_variables():
    token = var.set("req-123")
    try:
        assert runtime.submit_heavy(var.get).result(timeout=5) == "req-123"
        assert runtime.submit_interactive(var.get).result(timeout=5) == "req-123"
    finally:
        var.reset(token)


def test_failure_is_on_the_future_and_log_has_only_the_exception_class(caplog):
    def boom():
        raise RuntimeError("secret detail")

    with caplog.at_level(logging.WARNING, logger="estate.runtime"):
        future = runtime.submit_heavy(boom)
        with pytest.raises(RuntimeError):
            future.result(timeout=5)
    record = next(r for r in caplog.records if r.name == "estate.runtime")
    line = JsonFormatter().format(record)
    assert "RuntimeError" in line and "secret detail" not in line
    # the failed task released its slot
    assert runtime.submit_heavy(lambda: 1).result(timeout=5) == 1


@pytest.mark.parametrize(
    ("total", "reserved", "name"),
    [(4, 0, "RESERVED"), (4, 4, "RESERVED"), (1, 1, "MAX_WORKERS"), (3, 5, "RESERVED")],
)
def test_bad_config_names_the_setting(total, reserved, name):
    with pytest.raises(ImproperlyConfigured, match=name):
        runtime.validate_limits(total, reserved)
    with sized(total, reserved), pytest.raises(ImproperlyConfigured):
        runtime.submit_heavy(lambda: None)


def test_defaults_are_four_and_one():
    from django.conf import settings

    assert settings.RUNTIME_EXECUTOR_MAX_WORKERS == 4
    assert settings.RUNTIME_EXECUTOR_RESERVED_WORKERS == 1


def test_run_heavy_awaits_without_blocking_the_loop():
    async def scenario():
        async def ticker():
            worst = 0.0
            end = time.perf_counter() + 0.5
            while time.perf_counter() < end:
                before = time.perf_counter()
                await asyncio.sleep(0.01)
                worst = max(worst, time.perf_counter() - before)
            return worst

        heavy = asyncio.create_task(runtime.run_heavy(time.sleep, 0.5))
        worst = await ticker()
        await heavy
        return worst

    assert asyncio.run(scenario()) < 0.2


@pytest.mark.urls("tests.runtime.urls")
def test_fast_request_completes_while_a_slow_heavy_request_is_waiting():
    async def scenario():
        client = AsyncClient()
        slow = asyncio.create_task(client.get("/slow"))
        await asyncio.sleep(0.1)
        started = time.perf_counter()
        response = await client.get("/fast")
        elapsed = time.perf_counter() - started
        assert (await slow).status_code == 200
        return response.status_code, elapsed

    status, elapsed = asyncio.run(scenario())
    assert status == 200 and elapsed < 0.2


@sized(3, 1)
def test_cancelled_queued_heavy_future_releases_its_slot():
    gate = threading.Event()
    busy = [runtime.submit_interactive(gate.wait, 5) for _ in range(3)]  # every worker occupied
    queued = runtime.submit_heavy(lambda: "never")
    assert queued.cancel()
    gate.set()
    for future in busy:
        future.result(timeout=5)
    first = runtime.submit_heavy(lambda: 1)
    second = runtime.submit_heavy(lambda: 2)
    assert (first.result(timeout=5), second.result(timeout=5)) == (1, 2)


@sized(3, 1)
def test_pool_submit_failure_releases_the_slot(monkeypatch):
    executor = runtime._get()
    real = executor._pool.submit
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("pool down")
        return real(*args, **kwargs)

    monkeypatch.setattr(executor._pool, "submit", flaky)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            runtime.submit_heavy(lambda: None)
    assert runtime.submit_heavy(lambda: 1).result(timeout=5) == 1
    assert runtime.submit_heavy(lambda: 2).result(timeout=5) == 2


def test_worker_closes_old_connections_after_each_task(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "close_old_connections", lambda: calls.append(1))
    runtime.submit_heavy(lambda: None).result(timeout=5)

    def boom():
        raise RuntimeError

    with pytest.raises(RuntimeError):
        runtime.submit_interactive(boom).result(timeout=5)
    deadline = time.time() + 2
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert len(calls) == 2


def _reload_base():
    import importlib

    from config.settings import base

    return importlib.reload(base)


def test_settings_validation_at_startup(monkeypatch):
    monkeypatch.setenv("RUNTIME_EXECUTOR_MAX_WORKERS", "6")
    try:
        assert _reload_base().RUNTIME_EXECUTOR_MAX_WORKERS == 6
        monkeypatch.setenv("RUNTIME_EXECUTOR_RESERVED_WORKERS", "6")
        with pytest.raises(ImproperlyConfigured, match="RESERVED"):
            _reload_base()
    finally:
        monkeypatch.undo()
        _reload_base()
