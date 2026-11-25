"""The publication allowance (AD-7): who may publish how many Listings a month.

Read-only. `quota_limit_at` resolves the effective-dated policy at the time of an attempt;
`allowance_for` counts the account's counted `PublicationEvent`s in the Karachi calendar month.
Nothing here writes: events are written by Story 2.15's publish service, policies by the seed
migration and Story 2.20.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db.models import Q

from apps.identity import selectors as identity
from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.listings.models import (
    COUNTED_KINDS,
    QUOTA_ACCOUNT_TYPES,
    ListingQuotaPolicy,
    PublicationEvent,
)

TIMEZONE_NAME = "Asia/Karachi"
KARACHI = ZoneInfo(TIMEZONE_NAME)  # a fixed UTC+5, no daylight saving


class QuotaPolicyMissing(RuntimeError):
    """No policy row covers the instant (the seed guarantees one; never shown to a client)."""


@dataclass(frozen=True)
class Allowance:
    limit: int
    used: int
    remaining: int
    period_start: datetime
    resets_at: datetime


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def quota_limit_at(account_type: str, at: datetime) -> int:
    """The monthly limit in force for `account_type` at `at` (the range is half-open). The
    exclusion constraint leaves at most one row; the ordering only makes the answer
    deterministic (the newer row) were that ever violated."""
    _require_aware(at, "at")
    policy = (
        ListingQuotaPolicy.objects.filter(account_type=account_type, effective_from__lte=at)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
        .order_by("effective_from")
        .values_list("monthly_limit", flat=True)
        .first()
    )
    if policy is None:
        raise QuotaPolicyMissing("no quota policy covers this account type and time")
    return policy


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The first instant of the Karachi calendar month of `now` and of the next one (aware)."""
    _require_aware(now, "now")
    local = now.astimezone(KARACHI)
    start = datetime(local.year, local.month, 1, tzinfo=KARACHI)
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return start, datetime(year, month, 1, tzinfo=KARACHI)


def allowance_for(account_id: uuid.UUID, account_type: str, now: datetime) -> Allowance:
    """The allowance of one account at `now` (the caller passes the clock)."""
    _require_aware(now, "now")
    start, resets_at = month_bounds(now)
    limit = quota_limit_at(account_type, now)
    used = PublicationEvent.objects.filter(
        account_id=account_id,
        counted=True,
        kind__in=COUNTED_KINDS,
        created_at__gte=start,
        created_at__lt=resets_at,
    ).count()
    return Allowance(limit, used, max(limit - used, 0), start, resets_at)


def owner_allowance(actor: Actor, now: datetime) -> Allowance:
    """The caller's own allowance. A Visitor is `unauthenticated`; an administrator, a system
    actor and a Deactivated account (no `view_drafts`) are `forbidden` here; over HTTP a
    Deactivated account has no session, so the route answers 401 before this runs.
    Suspended and Email-unverified owners may read it. Only the caller's data."""
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, Capability.VIEW_DRAFTS):
        raise DomainError("forbidden")
    account = identity.get_signed_in_account(actor.account_id)
    if account is None or account.type not in QUOTA_ACCOUNT_TYPES:
        raise DomainError("forbidden")
    return allowance_for(account.id, account.type, now)
