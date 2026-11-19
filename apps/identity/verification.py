"""Email verification tokens: generation, hashing and retention. Writes go through `services`.

The plain token exists only in the email link; the database holds its SHA-256 hash.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta

from apps.identity.models import EmailVerificationToken

TOKEN_TTL = timedelta(hours=24)
RETENTION = timedelta(days=7)
TOKEN_MAX_LENGTH = 200
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]+")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def well_formed(token) -> bool:
    return (
        isinstance(token, str)
        and 0 < len(token) <= TOKEN_MAX_LENGTH
        and _TOKEN_SHAPE.fullmatch(token) is not None
    )


def purge_expired(now: datetime, dry_run: bool) -> int:
    """Retention job: tokens used or expired more than 7 days ago."""
    cutoff = now - RETENTION
    stale = EmailVerificationToken.objects.filter(expires_at__lt=cutoff) | (
        EmailVerificationToken.objects.filter(used_at__lt=cutoff)
    )
    if dry_run:
        return stale.count()
    return stale.delete()[0]
