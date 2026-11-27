"""The persisted AI budget (AD-25): one row per provider and UTC day, counted in requests.

`AI_DAILY_BUDGET` is the provider's daily allowance. `AI_RESERVED_PERCENT` of it (at least half)
is kept for signed-in Listing creation: other traffic stops once it has used the rest, while
Listing creation may use anything still left. A call is counted when it is admitted, whether or
not the provider then answers.
"""

import math
import uuid
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction

from apps.kernel.models import AiBudgetLedger

RETENTION = timedelta(days=90)
MIN_RESERVED_PERCENT = 50

_CREATE = """
INSERT INTO kernel_aibudgetledger (id, provider, day, used, open_used)
VALUES (%s, %s, %s, 0, 0)
ON CONFLICT (provider, day) DO NOTHING
"""
# One statement: the row lock makes the check and the count atomic across requests.
_TAKE = """
UPDATE kernel_aibudgetledger
SET used = used + 1, open_used = open_used + %s
WHERE provider = %s AND day = %s AND used < %s AND (%s OR open_used < %s)
RETURNING used
"""


def validate_limits(daily: int, reserved_percent: int) -> None:
    if daily < 0:
        raise ImproperlyConfigured("AI_DAILY_BUDGET must not be negative.")
    if not MIN_RESERVED_PERCENT <= reserved_percent <= 100:
        raise ImproperlyConfigured("AI_RESERVED_PERCENT must be from 50 to 100.")


def open_limit(daily: int, reserved_percent: int) -> int:
    """What traffic outside the reserved partition may use."""
    return daily - math.ceil(daily * reserved_percent / 100)


def take(provider: str, *, reserved: bool, now: datetime | None = None) -> bool:
    """Count one call; False when the day's budget (or the open part of it) is used up.

    `reserved` is true only for signed-in Listing creation. A database error propagates.
    """
    daily = settings.AI_DAILY_BUDGET
    day = (now or datetime.now(UTC)).astimezone(UTC).date()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(_CREATE, [uuid.uuid4(), provider, day])
        cursor.execute(
            _TAKE,
            [
                0 if reserved else 1,
                provider,
                day,
                daily,
                reserved,
                open_limit(daily, settings.AI_RESERVED_PERCENT),
            ],
        )
        return cursor.fetchone() is not None


def purge_expired(now: datetime, dry_run: bool = False) -> int:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    rows = AiBudgetLedger.objects.filter(day__lt=(now - RETENTION).date())
    if dry_run:
        return rows.count()
    deleted, _ = rows.delete()
    return deleted
