"""Bounded runtime executor (AD-14).

One thread pool of `RUNTIME_EXECUTOR_MAX_WORKERS` threads. Heavy work (AI calls, image
re-encode, model inference) may hold at most total minus `RUNTIME_EXECUTOR_RESERVED_WORKERS`
of them, enforced by a semaphore, so the reserved workers are always free for interactive
work such as chat and sign-in. Heavy submission never blocks: it returns a future at once or
raises `ExecutorBusy`, and the caller falls back (FR-38) instead of queueing.

Work nobody waits for (an estimate after publication) goes to a separate single background
worker with a bounded queue, so it can never take the heavy slots a waiting user needs.

Tasks run with a copy of the caller's context variables (for example the request id). A
failing task leaves its exception on the future; only the exception class is logged.
"""

import asyncio
import contextvars
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import close_old_connections

logger = logging.getLogger("estate.runtime")

BACKGROUND_QUEUE_LIMIT = 100  # background tasks waiting or running; more are refused


class ExecutorBusy(Exception):
    """All heavy slots are in use; the caller should fall back rather than wait."""


def validate_limits(total: int, reserved: int) -> None:
    if total < 2:
        raise ImproperlyConfigured("RUNTIME_EXECUTOR_MAX_WORKERS must be at least 2.")
    if reserved < 1:
        raise ImproperlyConfigured("RUNTIME_EXECUTOR_RESERVED_WORKERS must be at least 1.")
    if reserved >= total:
        raise ImproperlyConfigured(
            "RUNTIME_EXECUTOR_RESERVED_WORKERS must be less than RUNTIME_EXECUTOR_MAX_WORKERS."
        )


class _Executor:
    def __init__(self, total: int, reserved: int) -> None:
        validate_limits(total, reserved)
        self._pool = ThreadPoolExecutor(max_workers=total, thread_name_prefix="estate-runtime")
        self._heavy_slots = threading.BoundedSemaphore(total - reserved)
        self._background = ThreadPoolExecutor(max_workers=1, thread_name_prefix="estate-background")
        self._background_slots = threading.BoundedSemaphore(BACKGROUND_QUEUE_LIMIT)

    def submit(self, fn: Callable[..., Any], args: tuple, kwargs: dict, *, heavy: bool) -> Future:
        return self._submit(self._pool, self._heavy_slots if heavy else None, fn, args, kwargs)

    def submit_background(self, fn: Callable[..., Any], args: tuple, kwargs: dict) -> Future:
        return self._submit(self._background, self._background_slots, fn, args, kwargs)

    def _submit(self, pool, slots, fn, args, kwargs) -> Future:
        if slots and not slots.acquire(blocking=False):
            raise ExecutorBusy("capacity is full")
        context = contextvars.copy_context()
        release = self._release_once(slots) if slots else None
        try:
            future = pool.submit(self._run, context, fn, args, kwargs, release)
        except BaseException:
            if release:
                release()
            raise
        if release:
            # Also fires on cancellation before a worker starts the task, which `_run` never sees.
            future.add_done_callback(lambda _future: release())
        return future

    @staticmethod
    def _release_once(slots) -> Callable[[], None]:
        lock = threading.Lock()
        released = False

        def release() -> None:
            nonlocal released
            with lock:
                if released:
                    return
                released = True
            slots.release()

        return release

    def _run(self, context, fn, args, kwargs, release):
        try:
            return context.run(fn, *args, **kwargs)
        except BaseException as exc:
            logger.warning(
                "runtime_task_failed",
                extra={"event": "runtime_task_failed", "exception_class": type(exc).__name__},
            )
            raise
        finally:
            close_old_connections()  # a worker thread must not keep its database connection open
            if release:
                release()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._background.shutdown(wait=True, cancel_futures=True)


_executor: _Executor | None = None
_lock = threading.Lock()


def _get() -> _Executor:
    global _executor
    if _executor is None:
        with _lock:
            if _executor is None:
                _executor = _Executor(
                    settings.RUNTIME_EXECUTOR_MAX_WORKERS,
                    settings.RUNTIME_EXECUTOR_RESERVED_WORKERS,
                )
    return _executor


def submit_heavy(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    """Run `fn` on a worker; raises `ExecutorBusy` at once when heavy capacity is full."""
    return _get().submit(fn, args, kwargs, heavy=True)


def submit_interactive(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    """Run short interactive work (chat, sign-in); not limited by the heavy slots."""
    return _get().submit(fn, args, kwargs, heavy=False)


def submit_background(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    """Queue work nobody waits for on the one background worker; raises `ExecutorBusy` when
    `BACKGROUND_QUEUE_LIMIT` tasks are already queued."""
    return _get().submit_background(fn, args, kwargs)


async def run_heavy(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Await heavy work from an async view without holding a thread; may raise `ExecutorBusy`."""
    return await asyncio.wrap_future(submit_heavy(fn, *args, **kwargs))


def shutdown() -> None:
    """Stop the executor and forget it; the next submit builds a new one. For tests."""
    global _executor
    with _lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown()
