"""Email change tokens: lifetime and retention. Token helpers are shared with verification.

Generation, hashing and the shape check live in `identity.verification`; writes go through
`services`. The plain token exists only in the email link to the new address.
"""

from datetime import datetime, timedelta

from django.db.models import Q

from apps.identity.models import EmailChangeToken

TOKEN_TTL = timedelta(hours=24)
RETENTION = timedelta(days=7)


def purge_expired(now: datetime, dry_run: bool) -> int:
    """Retention job: tokens used or expired more than 7 days ago."""
    cutoff = now - RETENTION
    stale = EmailChangeToken.objects.filter(Q(expires_at__lt=cutoff) | Q(used_at__lt=cutoff))
    if dry_run:
        return stale.count()
    return stale.delete()[0]
