"""Governance retention (PRD 8.3, Story 10.12), run by `run_retention` (registered in `apps.py`).

Open Reports, pending requests and open support requests are never candidates. Reports,
Verification requests and moderation records are deleted with the purge role, because their
history tables are append-only for the runtime role.
"""

from datetime import datetime

from django.db.models import Q

from apps.governance.models import (
    ClosureRequest,
    ModerationAction,
    Report,
    SupportRequest,
    VerificationRequest,
)
from apps.kernel import retention

RECORD_MONTHS = 24
SUPPORT_MONTHS = 12


def reports(now: datetime, dry_run: bool) -> int:
    """Reports, their evidence snapshots and review history, 24 months after resolution."""
    closed = Report.objects.filter(
        status__in=["resolved", "dismissed"],
        closed_at__lt=retention.months_ago(now, RECORD_MONTHS),
    )
    return retention.delete_with_purge_role(closed, dry_run)


def verification(now: datetime, dry_run: bool) -> int:
    """24 months after the decision. A request that is not in force goes, with its history. The
    approval still in force stays (the Agency's status and revocation depend on it); only its
    evidence, the registration number and note, is emptied. Returns requests deleted or emptied."""
    decided = VerificationRequest.objects.filter(
        decided_at__lt=retention.months_ago(now, RECORD_MONTHS)
    )
    over = decided.filter(status__in=["rejected", "revoked", "withdrawn"])
    in_force = decided.filter(status="verified").filter(~Q(registration_number="") | ~Q(note=""))
    emptied = in_force.count() if dry_run else in_force.update(registration_number="", note="")
    return emptied + retention.delete_with_purge_role(over, dry_run)


def moderation_records(now: datetime, dry_run: bool) -> int:
    old = ModerationAction.objects.filter(created_at__lt=retention.months_ago(now, RECORD_MONTHS))
    return retention.delete_with_purge_role(old, dry_run)


def support_requests(now: datetime, dry_run: bool) -> int:
    old = SupportRequest.objects.filter(
        status="closed", closed_at__lt=retention.months_ago(now, SUPPORT_MONTHS)
    )
    return old.count() if dry_run else old.delete()[0]


def closure_requests(now: datetime, dry_run: bool) -> int:
    """Decided account closure requests, 24 months after the decision (like other decisions)."""
    old = ClosureRequest.objects.filter(
        status__in=["approved", "rejected"],
        decided_at__lt=retention.months_ago(now, RECORD_MONTHS),
    )
    return old.count() if dry_run else old.delete()[0]
