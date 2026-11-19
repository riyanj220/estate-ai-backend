"""Session tokens, lifetimes and retention. Writes go through `services`.

The plain token exists only in the cookie; the database holds its SHA-256 hash.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Q

from apps.identity.models import Session

COOKIE_NAME = "estate_session"
RETENTION = timedelta(days=7)
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{43}")  # token_urlsafe(32)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def well_formed(token) -> bool:
    return isinstance(token, str) and _TOKEN_SHAPE.fullmatch(token) is not None


def absolute_lifetime() -> timedelta:
    return timedelta(seconds=settings.SESSION_ABSOLUTE_SECONDS)


def idle_lifetime() -> timedelta:
    return timedelta(seconds=settings.SESSION_IDLE_SECONDS)


def touch_interval() -> timedelta:
    return timedelta(seconds=settings.SESSION_TOUCH_SECONDS)


def purge_stale(now: datetime, dry_run: bool) -> int:
    """Retention job: sessions ended, expired or idled out more than 7 days ago."""
    cutoff = now - RETENTION
    stale = Session.objects.filter(
        Q(ended_at__lt=cutoff)
        | Q(expires_at__lt=cutoff)
        | Q(last_seen_at__lt=cutoff - idle_lifetime())
    )
    if dry_run:
        return stale.count()
    return stale.delete()[0]
