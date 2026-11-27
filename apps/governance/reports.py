"""Reporting a Listing and reviewing Reports (Stories 9.1 and 9.2).

A Report never changes a Listing. Review moves it pending -> reviewing -> resolved or dismissed;
each move is recorded with notes and audited, and the reporter is notified of the outcome.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.governance.models import (
    OPEN_REPORT_STATUSES,
    REPORT_REASONS,
    REPORT_STATUSES,
    Report,
    ReportTransition,
)
from apps.governance.services import FieldsInvalid
from apps.identity import selectors as identity
from apps.identity.capabilities import Access, Capability, matrix_access
from apps.identity.policy import can
from apps.kernel import audit, notifications, ratelimit
from apps.kernel.actor import Actor, ActorKind, require_admin, require_signed_in
from apps.kernel.errors import DomainError
from apps.listings import selectors as listings

PAGE_SIZE = 20
REASONS = tuple(REPORT_REASONS)
STATUSES = tuple(REPORT_STATUSES)
DESCRIPTION_MAX = 1000
NOTES_MIN, NOTES_MAX = 5, 1000
_LISTING = "listing"
# Which status each administrator move starts from.
_MOVES = {"reviewing": "pending", "resolved": "reviewing", "dismissed": "reviewing"}


class InvalidCursor(Exception):
    pass


@dataclass(frozen=True)
class OwnReport:
    """What the reporter may see of their own Report (never the review notes)."""

    id: uuid.UUID
    listing_id: uuid.UUID
    title: str | None
    reason: str
    description: str = field(repr=False)
    status: str
    created_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class ReportRow:
    id: uuid.UUID
    listing_id: uuid.UUID
    title: str | None
    reason: str
    status: str
    created_at: datetime


@dataclass(frozen=True)
class ReportQueue:
    items: list[ReportRow]
    next_cursor: str | None
    pending: int
    reviewing: int


@dataclass(frozen=True)
class Move:
    to_status: str
    notes: str
    at: datetime
    by_account_id: uuid.UUID


@dataclass(frozen=True)
class ReportDetail:
    id: uuid.UUID
    listing_id: uuid.UUID
    reason: str
    status: str
    description: str = field(repr=False)
    snapshot: dict = field(repr=False)
    created_at: datetime
    closed_at: datetime | None
    reporter: identity.MaskedContact | None
    owner: identity.MaskedContact | None
    owner_account_id: uuid.UUID
    history: list[Move]
    open_for_listing: int  # open Reports on the same Listing, this one included


def _title(row: Report) -> str | None:
    return row.snapshot.get("title")


def _own(row: Report) -> OwnReport:
    return OwnReport(
        row.id,
        row.target_id,
        _title(row),
        row.reason,
        row.description,
        row.status,
        row.created_at,
        row.closed_at,
    )


def _clean_description(value) -> str | None:
    """The optional description, or None when it is not acceptable text."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    value = value.replace("\r\n", "\n").strip()
    if any(not (c.isprintable() or c == "\n") for c in value):
        return None
    return value


def _snapshot(actor: Actor, listing_id) -> dict:
    """The public view of the Listing as JSON: exactly what anyone could see, no advertiser
    contact, Private address or owner."""
    entry = listings.public_detail(actor, listing_id)
    view = entry.listing
    return {
        "purpose": view.purpose,
        "category": view.category,
        "subtype": view.subtype,
        "title": view.title,
        "description": view.description,
        "fields": {
            name: dict(value) if hasattr(value, "items") else value
            for name, value in view.fields.items()
            if value is not None and name not in ("city", "locality")
        },
        "city": entry.city.name if entry.city else None,
        "locality": entry.locality.name if entry.locality else None,
        "image_count": len(view.media),
    }


def report_listing(actor: Actor, listing_id, reason, description=None) -> OwnReport:
    """Report a publicly visible Listing of another account.

    Visitor `unauthenticated`; an account without the `report` capability (Suspended,
    Deactivated, administrator) `forbidden`; bad values `FieldsInvalid`; a Listing that is not
    public `not_found`; your own `forbidden`; one already open `conflict`; more than the daily
    limit `throttled`. Nothing about the Listing changes."""
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, Capability.REPORT):
        raise DomainError("forbidden")
    errors = {}
    if reason is None or reason == "":
        errors["reason"] = "required"
    elif reason not in REASONS:
        errors["reason"] = "invalid_choice"
    text = _clean_description(description)
    if text is None:
        errors["description"] = "invalid"
    elif len(text) > DESCRIPTION_MAX:
        errors["description"] = "too_long"
    if errors:
        raise FieldsInvalid(errors)
    with transaction.atomic():
        account = identity.lock_account(actor.account_id)  # serialises this account's Reports
        if matrix_access(account.state, Capability.REPORT) is not Access.ALLOWED:
            raise DomainError("forbidden")
        snapshot = _snapshot(actor, listing_id)  # `not_found` unless public
        key = listings.canonical_uuid(listing_id)
        owner = listings.public_owner_id(Actor.system(actor.request_id), key)
        if owner == actor.account_id:
            raise DomainError("forbidden")
        if Report.objects.filter(
            reporter_account_id=actor.account_id,
            target_type=_LISTING,
            target_id=key,
            status__in=OPEN_REPORT_STATUSES,
        ).exists():
            raise DomainError("conflict")
        ratelimit.enforce("report_new", ratelimit.derive_key("account", str(actor.account_id)))
        now = timezone.now()
        row = Report.objects.create(
            target_type=_LISTING,
            target_id=key,
            reporter_account_id=actor.account_id,
            subject_account_id=owner,
            reason=reason,
            description=text,
            snapshot=snapshot,
            created_at=now,
            updated_at=now,
        )
        audit.record(actor, "report.submitted", "report", row.id)
    return _own(row)


def own_report(actor: Actor, report_id) -> OwnReport:
    """The caller's own Report; anyone else's, unknown and malformed ids are `not_found`."""
    require_signed_in(actor)
    key = identity.parse_id(report_id)
    row = (
        Report.objects.filter(id=key, reporter_account_id=actor.account_id).first()
        if key is not None and actor.kind is ActorKind.USER
        else None
    )
    if row is None:
        raise DomainError("not_found")
    return _own(row)


def under_review(listing_ids) -> set[uuid.UUID]:
    """Which of these Listings have a Report in `reviewing` (a derived indicator for the owner
    and administrators, composed in presentation; it changes nothing)."""
    return set(
        Report.objects.filter(
            target_type=_LISTING, target_id__in=list(listing_ids), status="reviewing"
        ).values_list("target_id", flat=True)
    )


def report_queue(
    actor: Actor, status: str = "pending", reason: str | None = None, cursor: str | None = None
) -> ReportQueue:
    """Listing Reports in one status, oldest first, with the unresolved counts."""
    require_admin(actor)
    if status not in STATUSES or (reason is not None and reason not in REASONS):
        raise DomainError("validation_error")
    everything = Report.objects.filter(target_type=_LISTING)
    rows = everything.filter(status=status).order_by("created_at", "id")
    if reason is not None:
        rows = rows.filter(reason=reason)
    if cursor is not None:
        key = identity.parse_id(cursor)
        last = everything.filter(id=key).first() if key else None
        if last is None:
            raise InvalidCursor
        rows = rows.filter(
            Q(created_at__gt=last.created_at) | Q(created_at=last.created_at, id__gt=last.id)
        )
    page = list(rows[: PAGE_SIZE + 1])
    items = page[:PAGE_SIZE]
    counts = dict(
        everything.filter(status__in=OPEN_REPORT_STATUSES)
        .values_list("status")
        .annotate(total=Count("id"))
        .order_by()
    )
    return ReportQueue(
        [ReportRow(r.id, r.target_id, _title(r), r.reason, r.status, r.created_at) for r in items],
        str(items[-1].id) if len(page) > PAGE_SIZE else None,
        counts.get("pending", 0),
        counts.get("reviewing", 0),
    )


def open_report_count(actor: Actor, listing_id) -> int:
    require_admin(actor)
    return Report.objects.filter(
        target_type=_LISTING, target_id=listing_id, status__in=OPEN_REPORT_STATUSES
    ).count()


def _listing_report(report_id, *, lock: bool = False) -> Report:
    key = identity.parse_id(report_id)
    rows = Report.objects.select_for_update() if lock else Report.objects
    # Conversation Reports hold Message content: Story 9.5 adds their reasoned read.
    row = rows.filter(id=key, target_type=_LISTING).first() if key else None
    if row is None:
        raise DomainError("not_found")
    return row


def report_detail(actor: Actor, report_id) -> ReportDetail:
    """A Listing Report with its snapshot (public content, so no reasoned read)."""
    require_admin(actor)
    row = _listing_report(report_id)
    contacts = identity.masked_contacts([row.reporter_account_id, row.subject_account_id])
    return ReportDetail(
        id=row.id,
        listing_id=row.target_id,
        reason=row.reason,
        status=row.status,
        description=row.description,
        snapshot=row.snapshot,
        created_at=row.created_at,
        closed_at=row.closed_at,
        reporter=contacts.get(row.reporter_account_id),
        owner=contacts.get(row.subject_account_id),
        owner_account_id=row.subject_account_id,
        history=[
            Move(t.to_status, t.notes, t.created_at, t.actor_account_id)
            for t in row.transitions.order_by("created_at", "id")
        ],
        open_for_listing=open_report_count(actor, row.target_id),
    )


def _clean_notes(notes, *, required: bool) -> str:
    text = _clean_description(notes)
    if text is None:
        raise FieldsInvalid({"notes": "invalid"})
    if not text:
        if required:
            raise FieldsInvalid({"notes": "required"})
        return ""
    if len(text) < NOTES_MIN:
        raise FieldsInvalid({"notes": "too_short"})
    if len(text) > NOTES_MAX:
        raise FieldsInvalid({"notes": "too_long"})
    return text


def _move(actor: Actor, report_id, to_status: str, notes) -> ReportDetail:
    require_admin(actor)
    text = _clean_notes(notes, required=to_status != "reviewing")
    with transaction.atomic():
        row = _listing_report(report_id, lock=True)
        if row.status != _MOVES[to_status]:
            raise DomainError("conflict")
        now = timezone.now()
        row.status = to_status
        row.updated_at = now
        row.closed_at = now if to_status != "reviewing" else None
        row.save(update_fields=["status", "updated_at", "closed_at"])
        ReportTransition.objects.create(
            report=row,
            to_status=to_status,
            actor_account_id=actor.account_id,
            notes=text,
            created_at=now,
        )
        action = "review_started" if to_status == "reviewing" else to_status
        audit.record(actor, f"report.{action}", "report", row.id)
        if to_status != "reviewing":
            notifications.notify(row.reporter_account_id, f"report.{to_status}", {})  # last
    return report_detail(actor, row.id)


def start_review(actor: Actor, report_id, notes=None) -> ReportDetail:
    return _move(actor, report_id, "reviewing", notes)


def resolve_report(actor: Actor, report_id, notes) -> ReportDetail:
    return _move(actor, report_id, "resolved", notes)


def dismiss_report(actor: Actor, report_id, notes) -> ReportDetail:
    return _move(actor, report_id, "dismissed", notes)
