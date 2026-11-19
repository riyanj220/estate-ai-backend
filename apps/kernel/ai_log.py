"""AI request log (AD-8, AD-26): feature, provider, model, latency, outcome.

By design the log stores no content, account id, prompt, response or free text. It constrains
the shape of its fields (short lowercase tokens, a fixed outcome set) to make slipping content
in hard, but it cannot prove that a well-formed token carries no content.

`record_ai_call` raises `ValueError` on invalid input and can raise on a database error. Logging
must never fail the user-facing AI call, so callers (the future gateway) guard it: catch,
log the exception class and carry on. Provider and model ids from outside should go through
`normalise_token` first.
"""

import re
from datetime import datetime, timedelta

from django.utils import timezone

from apps.kernel.models import AI_OUTCOMES, AiRequestLog

RETENTION = timedelta(days=90)  # PRD section 8.3
OUTCOMES = frozenset(AI_OUTCOMES)
MAX_LATENCY_MS = 2147483647  # PostgreSQL integer maximum
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,47}$")
_INVALID = re.compile(r"[^a-z0-9_.-]")


def normalise_token(value: str) -> str:
    """Lowercase, replace invalid characters with `-`, trim leading separators, cap at 48.

    Turns ids such as `Meta-Llama/Llama-3.3:70B` into `meta-llama-llama-3.3-70b`.
    """
    token = _INVALID.sub("-", str(value).lower()).lstrip("-._")
    return token[:48]


def record_ai_call(
    feature: str,
    provider: str,
    model: str,
    latency_ms: int,
    outcome: str,
    now: datetime | None = None,
) -> None:
    for label, value in (("feature", feature), ("provider", provider), ("model", model)):
        if not isinstance(value, str) or not _TOKEN.fullmatch(value):
            raise ValueError(f"{label} must be a short lowercase token")
    if outcome not in OUTCOMES:
        raise ValueError("unknown outcome")
    if (
        isinstance(latency_ms, bool)
        or not isinstance(latency_ms, int)
        or not 0 <= latency_ms <= MAX_LATENCY_MS
    ):
        raise ValueError("latency_ms must be an integer from 0 to 2147483647")
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("now must be timezone-aware")
    AiRequestLog.objects.create(
        feature=feature,
        provider=provider,
        model=model,
        latency_ms=latency_ms,
        outcome=outcome,
        created_at=now or timezone.now(),
    )


def purge_expired(now: datetime, dry_run: bool = False) -> int:
    """Delete (or in dry-run only count) rows older than the retention window."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    rows = AiRequestLog.objects.filter(created_at__lt=now - RETENTION)
    if dry_run:
        return rows.count()
    deleted, _ = rows.delete()
    return deleted
