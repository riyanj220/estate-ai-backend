"""Reads for Agency Verification (Stories 8.3 and 8.4), and the reasoned read of a
Conversation Report (Story 9.5)."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import Q

from apps.governance.models import (
    REQUEST_STATUSES,
    Report,
    VerificationDecision,
    VerificationRequest,
)
from apps.identity import selectors as identity
from apps.identity import services as identity_services
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError
from apps.kernel.privacy.reads import record_private_read

PAGE_SIZE = 20
STATUSES = tuple(REQUEST_STATUSES)


class InvalidCursor(Exception):
    pass


@dataclass(frozen=True)
class OwnRequest:
    business_name: str
    registration_number: str = field(repr=False)
    note: str = field(repr=False)
    submitted_at: datetime
    decided_at: datetime | None
    decision_reason: str


@dataclass(frozen=True)
class OwnVerification:
    status: str  # `unverified` or a request status
    can_submit: bool
    can_withdraw: bool
    request: OwnRequest | None  # the latest one


@dataclass(frozen=True)
class QueueRow:
    id: uuid.UUID
    status: str
    business_name: str
    agency_name: str | None  # None when the account is gone
    agency_id: uuid.UUID | None
    account_state: str | None
    submitted_at: datetime


@dataclass(frozen=True)
class QueuePage:
    items: list[QueueRow]
    next_cursor: str | None


@dataclass(frozen=True)
class HistoryEntry:
    action: str
    at: datetime
    by_account_id: uuid.UUID
    reason: str


@dataclass(frozen=True)
class RequestDetail:
    id: uuid.UUID
    status: str
    business_name: str
    registration_number: str = field(repr=False)
    note: str = field(repr=False)
    submitted_at: datetime
    decided_at: datetime | None
    decision_reason: str
    agency_name: str | None
    agency_id: uuid.UUID | None
    account_state: str | None
    history: list[HistoryEntry]  # every decision about this Agency, newest first


def parse_request_id(raw) -> uuid.UUID | None:
    return identity.parse_id(raw)


def own_verification(actor: Actor) -> OwnVerification:
    """The signed-in Agency's own status. Other account types are `forbidden`; a Suspended
    Agency may read."""
    identity_services.require_agency(actor)
    facts = identity.agency_facts([actor.account_id]).get(actor.account_id)
    if facts is None:
        raise DomainError("forbidden")
    row = (
        VerificationRequest.objects.filter(agency_account_id=actor.account_id)
        .order_by("-submitted_at", "-id")
        .first()
    )
    status = "verified" if facts.verified else (row.status if row else "unverified")
    if status == "verified" and not facts.verified:
        status = "unverified"  # the identity flag is the authority
    may_act = facts.state in (identity.AccountState.ACTIVE, identity.AccountState.EMAIL_UNVERIFIED)
    pending = status == "pending"
    return OwnVerification(
        status=status,
        can_submit=may_act and not pending and not facts.verified,
        can_withdraw=may_act and pending,
        request=OwnRequest(
            row.business_name,
            row.registration_number,
            row.note,
            row.submitted_at,
            row.decided_at,
            row.decision_reason,
        )
        if row
        else None,
    )


def review_queue(actor: Actor, status: str = "pending", cursor: str | None = None) -> QueuePage:
    """Requests in one status, oldest first, without the private fields."""
    require_admin(actor)
    if status not in STATUSES:
        raise DomainError("validation_error")
    rows = VerificationRequest.objects.filter(status=status).order_by("submitted_at", "id")
    if cursor is not None:
        key = parse_request_id(cursor)
        last = VerificationRequest.objects.filter(id=key).first() if key else None
        if last is None:
            raise InvalidCursor
        rows = rows.filter(
            Q(submitted_at__gt=last.submitted_at)
            | Q(submitted_at=last.submitted_at, id__gt=last.id)
        )
    page = list(rows[: PAGE_SIZE + 1])
    items = page[:PAGE_SIZE]
    facts = identity.agency_facts(row.agency_account_id for row in items)
    out = []
    for row in items:
        agency = facts.get(row.agency_account_id)
        out.append(
            QueueRow(
                row.id,
                row.status,
                row.business_name,
                agency.agency_name if agency else None,
                agency.agency_id if agency else None,
                agency.state.value if agency else None,
                row.submitted_at,
            )
        )
    return QueuePage(out, str(items[-1].id) if len(page) > PAGE_SIZE else None)


def request_id_for_read(actor: Actor, raw) -> uuid.UUID:
    """The id of an existing request, else `not_found`; reads nothing else."""
    require_admin(actor)
    key = parse_request_id(raw)
    if key is None or not VerificationRequest.objects.filter(id=key).exists():
        raise DomainError("not_found")
    return key


def admin_read_verification_request(
    actor: Actor, reason: str, request_id: uuid.UUID
) -> RequestDetail:
    """A reasoned private read (`docs/audit.md`) of one request's submitted details."""
    record_private_read(actor, reason, ("verification_request", request_id))
    row = VerificationRequest.objects.filter(id=request_id).first()
    if row is None:
        raise DomainError("not_found")
    agency = identity.agency_facts([row.agency_account_id]).get(row.agency_account_id)
    decisions = VerificationDecision.objects.filter(
        request__agency_account_id=row.agency_account_id
    ).order_by("-created_at", "-id")
    return RequestDetail(
        id=row.id,
        status=row.status,
        business_name=row.business_name,
        registration_number=row.registration_number,
        note=row.note,
        submitted_at=row.submitted_at,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        agency_name=agency.agency_name if agency else None,
        agency_id=agency.agency_id if agency else None,
        account_state=agency.state.value if agency else None,
        history=[
            HistoryEntry(d.action, d.created_at, d.actor_account_id, d.reason) for d in decisions
        ],
    )


@dataclass(frozen=True)
class ReportContent:
    description: str = field(repr=False)
    messages: list[dict] = field(repr=False)  # seq, sender, selected, body, sent_at


def admin_read_conversation_report(actor: Actor, reason: str, report_id: uuid.UUID):
    """A reasoned private read (`docs/audit.md`) of one Conversation Report's snapshot: the
    reported Messages and the few before them, never the rest of the Conversation."""
    record_private_read(actor, reason, ("report_snapshot", report_id))
    row = Report.objects.filter(id=report_id, target_type="conversation").first()
    if row is None:
        raise DomainError("not_found")
    return ReportContent(row.description, row.snapshot["messages"])
