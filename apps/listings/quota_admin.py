"""Administrator management of the quota policy (Story 2.20, FR-60).

The current limits, this month's usage by account, and a change of one account type's limit that
takes effect from now. A change closes the open `ListingQuotaPolicy` row and inserts a new one;
no other row is ever modified, and `listings.quota` keeps resolving the limit at the time of each
attempt. Every function requires an administrator.
"""

import base64
import binascii
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from django.db import connection, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.identity import selectors as identity
from apps.kernel import audit
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError
from apps.listings import quota
from apps.listings.models import (
    COUNTED_KINDS,
    QUOTA_ACCOUNT_TYPES,
    ListingQuotaPolicy,
    PublicationEvent,
)
from apps.listings.selectors import DEFAULT_LIMIT, MAX_LIMIT, InvalidCursor, canonical_uuid
from apps.listings.services import FieldsInvalid

ACCOUNT_TYPES = QUOTA_ACCOUNT_TYPES  # for presentation, which may not import the models
MAX_MONTHLY_LIMIT = 1000
_CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


@dataclass(frozen=True)
class PolicyView:
    account_type: str
    monthly_limit: int
    effective_from: datetime  # in Asia/Karachi


@dataclass(frozen=True)
class UsageRow:
    account_id: uuid.UUID
    email: str  # masked: `identity.selectors.mask_email`
    account_type: str
    used: int
    limit: int


@dataclass(frozen=True)
class UsagePage:
    items: list[UsageRow]
    next_cursor: str | None
    period_start: datetime
    resets_at: datetime


def _require_administrator(actor: Actor) -> None:
    """Visitor `unauthenticated`, any other non-administrator `not_found`; an administrator whose
    account is gone or Deactivated is `unauthenticated`."""
    require_admin(actor)
    view = identity.get_signed_in_account(actor.account_id)
    if view is None or view.type != identity.ADMIN_ACCOUNT_TYPE:
        raise DomainError("unauthenticated")


def _view(row: ListingQuotaPolicy) -> PolicyView:
    return PolicyView(
        row.account_type, row.monthly_limit, row.effective_from.astimezone(quota.KARACHI)
    )


def current_policies(actor: Actor, now: datetime | None = None) -> list[PolicyView]:
    """The row in force now for each account type, in `QUOTA_ACCOUNT_TYPES` order."""
    _require_administrator(actor)
    now = now if now is not None else timezone.now()
    rows = {
        row.account_type: row
        for row in ListingQuotaPolicy.objects.filter(effective_from__lte=now).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gt=now)
        )
    }
    return [_view(rows[kind]) for kind in QUOTA_ACCOUNT_TYPES if kind in rows]


def _lock_type(account_type: str) -> None:
    """One lock per account type until the transaction ends. A row lock alone is not enough: a
    waiter would find the row it waited on closed and would not see the one just inserted."""
    digest = hashlib.sha256(f"listings.quota_policy:{account_type}".encode()).digest()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)", [int.from_bytes(digest[:8], "big", signed=True)]
        )


def _limit_error(value) -> str | None:
    if value is None:
        return "required"
    if isinstance(value, bool) or not isinstance(value, int):
        return "invalid"
    if value < 0:
        return "min_value"
    if value > MAX_MONTHLY_LIMIT:
        return "max_value"
    return None


def set_quota_limit(
    actor: Actor, account_type: str, monthly_limit: int, now: datetime | None = None
) -> PolicyView:
    """Change one account type's monthly limit from `now` on and return the current row.

    Refusals: the administrator guard; an unknown account type `not_found`; a limit that is not
    a whole number from 0 to 1000 `FieldsInvalid`; a `now` not after the current row's start
    `conflict`. The current limit again changes and audits nothing. The clock is read after the
    lock unless the caller passes `now`, so serialised changes keep increasing times."""
    _require_administrator(actor)
    if account_type not in QUOTA_ACCOUNT_TYPES:
        raise DomainError("not_found")
    if error := _limit_error(monthly_limit):
        raise FieldsInvalid({"monthly_limit": error})
    with transaction.atomic():
        _lock_type(account_type)
        current = (
            ListingQuotaPolicy.objects.select_for_update()
            .filter(account_type=account_type, effective_to__isnull=True)
            .first()
        )
        if current is None:
            raise quota.QuotaPolicyMissing("no open quota policy for this account type")
        if current.monthly_limit == monthly_limit:
            return _view(current)
        now = now if now is not None else timezone.now()
        if now <= current.effective_from:
            raise DomainError("conflict")
        # The exclusion constraint is not deferrable: close first, then insert.
        current.effective_to = now
        current.save(update_fields=["effective_to"])
        new = ListingQuotaPolicy.objects.create(
            account_type=account_type, monthly_limit=monthly_limit, effective_from=now
        )
        audit.record(
            actor,
            "quota.policy_changed",
            "quota_policy",
            new.id,
            {
                "account_type": account_type,
                "old_limit": current.monthly_limit,
                "new_limit": monthly_limit,
            },
        )
    return _view(new)


def _encode_cursor(period_start: datetime, latest: datetime, account_id: uuid.UUID) -> str:
    raw = json.dumps(
        {"p": period_start.isoformat(), "t": latest.isoformat(), "i": str(account_id)},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(
    cursor: str, period_start: datetime, resets_at: datetime
) -> tuple[datetime, uuid.UUID]:
    """The position in a cursor issued for the same month."""
    if not _CURSOR.fullmatch(cursor):
        raise InvalidCursor
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(data, dict) or set(data) != {"p", "t", "i"}:
            raise InvalidCursor
        latest = datetime.fromisoformat(data["t"])
        account_id = canonical_uuid(data["i"])
        if latest.tzinfo is None or account_id is None or data["p"] != period_start.isoformat():
            raise InvalidCursor
        if not period_start <= latest < resets_at:
            raise InvalidCursor
    except (binascii.Error, ValueError, TypeError, OverflowError, RecursionError):
        raise InvalidCursor from None
    return latest.astimezone(UTC), account_id


def usage_by_account(
    actor: Actor,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
) -> UsagePage:
    """The accounts with at least one counted publication in the Karachi month of `now`, newest
    usage first (then account id), keyset-paginated. A bad cursor, or one from another month,
    raises `InvalidCursor`. An account that publishes between two pages moves to the front."""
    _require_administrator(actor)
    now = now if now is not None else timezone.now()
    start, resets_at = quota.month_bounds(now)
    limit = min(max(limit, 1), MAX_LIMIT)
    rows = (
        PublicationEvent.objects.filter(
            counted=True,
            kind__in=COUNTED_KINDS,
            created_at__gte=start,
            created_at__lt=resets_at,
            account_id__in=identity.owner_account_ids(),
        )
        .values("account_id")
        .annotate(used=Count("id"), latest=Max("created_at"))
        .order_by("-latest", "-account_id")
    )
    if cursor is not None:
        latest, last_id = _decode_cursor(cursor, start, resets_at)
        rows = rows.filter(Q(latest__lt=latest) | Q(latest=latest, account_id__lt=last_id))
    found = list(rows[: limit + 1])
    page = found[:limit]
    next_cursor = (
        _encode_cursor(start, page[-1]["latest"], page[-1]["account_id"])
        if len(found) > limit
        else None
    )
    contacts = identity.masked_contacts([row["account_id"] for row in page])
    limits = {kind: quota.quota_limit_at(kind, now) for kind in {c.type for c in contacts.values()}}
    items = [
        UsageRow(
            row["account_id"], contact.masked_email, contact.type, row["used"], limits[contact.type]
        )
        for row in page
        if (contact := contacts.get(row["account_id"])) is not None
    ]
    return UsagePage(items, next_cursor, start, resets_at)
