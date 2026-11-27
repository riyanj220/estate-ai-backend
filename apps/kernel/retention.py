"""Retention hooks (AD-23): apps register a purge job, `run_retention` runs them all.

An app registers its job from `AppConfig.ready`:

    retention.register("listings.drafts", purge_expired_drafts)

`purge(now, dry_run)` returns the number of rows deleted, or in dry-run the number that
would be deleted, and deletes nothing. The kernel never imports an app; apps come to it.

History the runtime role may not delete (append-only tables and the rows that cascade into them)
is deleted through the `purge` connection, whose database role holds only SELECT and DELETE on
`settings.PURGE_ROLE_TABLES` (Stories 10.12 and 10.13). Use `delete_with_purge_role`.
"""

import calendar
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet

logger = logging.getLogger("estate.retention")

Purge = Callable[[datetime, bool], int]
PURGE_ALIAS = "purge"
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


class PurgeRoleNotConfigured(RuntimeError):
    """There are rows to purge but no `purge` connection (PURGE_PASSWORD is not set)."""


def months_ago(now: datetime, months: int) -> datetime:
    """`now` minus whole calendar months; a day the target month lacks becomes its last day."""
    year, month = divmod(now.year * 12 + now.month - 1 - months, 12)
    month += 1
    return now.replace(
        year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1])
    )


def purge_alias() -> str:
    if PURGE_ALIAS not in settings.DATABASES:
        raise PurgeRoleNotConfigured("set PURGE_PASSWORD for the purge database role")
    return PURGE_ALIAS


def delete_with_purge_role(rows: QuerySet, dry_run: bool) -> int:
    """Delete `rows` (and what cascades from them) as the purge role, in its own transaction.
    Returns the number of `rows` themselves. Counting uses the ordinary connection, so a dry run,
    or a run with nothing to delete, needs no purge role."""
    count = rows.count()
    if dry_run or not count:
        return count
    alias = purge_alias()
    with transaction.atomic(using=alias):
        _, per_model = rows.using(alias).delete()
    return per_model.get(rows.model._meta.label, 0)
