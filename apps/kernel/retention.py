"""Retention hooks (AD-23): apps register a purge job, `run_retention` runs them all.

An app registers its job from `AppConfig.ready`:

    retention.register("listings.drafts", purge_expired_drafts)

`purge(now, dry_run)` returns the number of rows deleted, or in dry-run the number that
would be deleted, and deletes nothing. The kernel never imports an app; apps come to it.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction

logger = logging.getLogger("estate.retention")

Purge = Callable[[datetime, bool], int]
_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_jobs: dict[str, Purge] = {}


@dataclass(frozen=True)
class JobResult:
    name: str
    count: int | None
    error_class: str | None = None


def register(name: str, purge: Purge) -> None:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("job name must be lowercase letters, digits, underscores and dots")
    if not callable(purge):
        raise ValueError("purge must be callable")
    if _jobs.get(name) is purge:
        return  # same job again (for example `ready()` running twice)
    if name in _jobs:
        raise ValueError(f"retention job {name!r} is already registered")
    _jobs[name] = purge


def registered() -> list[str]:
    return list(_jobs)


def run_all(now: datetime, dry_run: bool = False) -> list[JobResult]:
    """Run every job in registration order, each in its own transaction; failures are isolated."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    results = []
    for name, purge in list(_jobs.items()):
        try:
            with transaction.atomic():
                count = purge(now, dry_run)
                if isinstance(count, bool) or not isinstance(count, int):
                    raise TypeError("a purge job must return an int row count")
        except Exception as exc:
            logger.error(
                "retention_job_failed",
                extra={
                    "event": "retention_job_failed",
                    "handler": name,
                    "exception_class": type(exc).__name__,
                },
            )
            results.append(JobResult(name, None, type(exc).__name__))
        else:
            results.append(JobResult(name, count))
    return results
