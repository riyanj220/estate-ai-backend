"""Session tokens, lifetimes and retention. Writes go through `services`.

The plain token exists only in the cookie; the database holds its SHA-256 hash.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Q

from apps.identity.models import AccountType, Session

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


def admin_absolute_lifetime() -> timedelta:
    return timedelta(seconds=settings.ADMIN_SESSION_ABSOLUTE_SECONDS)


def admin_idle_lifetime() -> timedelta:
    return timedelta(seconds=settings.ADMIN_SESSION_IDLE_SECONDS)


def absolute_lifetime_for(account_type: str) -> timedelta:
    """Administrators get the short limits (Story 1.22); everyone else the ordinary ones."""
    return admin_absolute_lifetime() if account_type == AccountType.ADMIN else absolute_lifetime()


def idle_lifetime_for(account_type: str) -> timedelta:
    return admin_idle_lifetime() if account_type == AccountType.ADMIN else idle_lifetime()


def touch_interval() -> timedelta:
    return timedelta(seconds=settings.SESSION_TOUCH_SECONDS)


def purge_stale(now: datetime, dry_run: bool) -> int:
    """Retention job: sessions ended, expired or idled out more than 7 days ago.

    The idle limit is the one of the session's account type, so a long-idle administrator row
    does not linger for the ordinary idle limit and a still-valid session is never touched.
    """
    cutoff = now - RETENTION
    stale = Session.objects.filter(
        Q(ended_at__lt=cutoff)
        | Q(expires_at__lt=cutoff)
        | (
            ~Q(account__account_type=AccountType.ADMIN)
            & Q(last_seen_at__lt=cutoff - idle_lifetime())
        )
        | (
            Q(account__account_type=AccountType.ADMIN)
            & Q(last_seen_at__lt=cutoff - admin_idle_lifetime())
        )
    )
    if dry_run:
        return stale.count()
    return stale.delete()[0]
