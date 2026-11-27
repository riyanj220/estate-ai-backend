"""Reporting a Listing or a Conversation, and reviewing Reports (Stories 9.1, 9.2 and 9.5).

A Report never changes a Listing or a Message. Review moves it pending -> reviewing -> resolved or
dismissed; each move is recorded with notes and audited, and the reporter is notified of the
outcome.

A Conversation Report keeps a snapshot of the reported Messages and a few before them. That text
and the reporter's description are private: no read here returns them to an administrator (see
`selectors.admin_read_conversation_report`). When a participant account is deactivated the
snapshot is anonymised.
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
from apps.kernel.privacy.patterns import find_contact_details
from apps.listings import selectors as listings
from apps.messaging import selectors as messaging

PAGE_SIZE = 20
REASONS = tuple(REPORT_REASONS)
STATUSES = tuple(REPORT_STATUSES)
DESCRIPTION_MAX = 1000
NOTES_MIN, NOTES_MAX = 5, 1000
SELECTED_MAX = messaging.REPORT_SELECTED_MAX
_LISTING = "listing"
_CONVERSATION = "conversation"
TARGETS = (_LISTING, _CONVERSATION)
DELETED_SENDER = "deleted_user"
_REMOVED = "[removed]"
_SYSTEM = Actor.system("governance")
# Which status each administrator move starts from.
_MOVES = {"reviewing": "pending", "resolved": "reviewing", "dismissed": "reviewing"}


class InvalidCursor(Exception):
    pass


@dataclass(frozen=True)
class OwnReport:
    """What the reporter may see of their own Report (never the review notes)."""

    id: uuid.UUID
    target_type: str
    listing_id: uuid.UUID
    conversation_id: uuid.UUID | None
    title: str | None
    reason: str
    description: str = field(repr=False)
    status: str
    created_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class ReportRow:
    id: uuid.UUID
    target_type: str
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
    """For a Conversation Report `description` is empty and `snapshot` and `messages` are None
    until the reasoned read fills `description` and `messages`. `owner` is the reported account."""

    id: uuid.UUID
    target_type: str
    listing_id: uuid.UUID
    conversation_id: uuid.UUID | None
    reason: str
    status: str
    description: str = field(repr=False)
    snapshot: dict | None = field(repr=False)
    messages: list[dict] | None = field(repr=False)
    created_at: datetime
    closed_at: datetime | None
    reporter: identity.MaskedContact | None
    owner: identity.MaskedContact | None
    owner_account_id: uuid.UUID
    history: list[Move]
    open_for_listing: int  # open Reports on the same target, this one included


def _title(row: Report) -> str | None:
    return row.snapshot.get("title")


def _listing_id(row: Report) -> uuid.UUID:
    return row.target_id if row.target_type == _LISTING else uuid.UUID(row.snapshot["listing_id"])


def _conversation_id(row: Report) -> uuid.UUID | None:
    return row.target_id if row.target_type == _CONVERSATION else None


def _own(row: Report) -> OwnReport:
    return OwnReport(
        row.id,
        row.target_type,
        _listing_id(row),
        _conversation_id(row),
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


def _require_reporter(actor: Actor) -> None:
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, Capability.REPORT):
        raise DomainError("forbidden")


def _clean_report(reason, description) -> tuple[dict[str, str], str]:
    """The field errors and the cleaned description."""
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
    return errors, text or ""


def _lock_reporter(actor: Actor) -> None:
    """Inside a transaction: lock the account (which serialises its Reports) and check again."""
    account = identity.lock_account(actor.account_id)
    if matrix_access(account.state, Capability.REPORT) is not Access.ALLOWED:
        raise DomainError("forbidden")


def _store(actor: Actor, target_type, target_id, subject, reason, text, snapshot) -> Report:
    """Inside the reporter's transaction: one open Report per target, the daily limit, the row."""
    if Report.objects.filter(
        reporter_account_id=actor.account_id,
        target_type=target_type,
        target_id=target_id,
        status__in=OPEN_REPORT_STATUSES,
    ).exists():
        raise DomainError("conflict")
    ratelimit.enforce("report_new", ratelimit.derive_key("account", str(actor.account_id)))
    now = timezone.now()
    row = Report.objects.create(
        target_type=target_type,
        target_id=target_id,
        reporter_account_id=actor.account_id,
        subject_account_id=subject,
        reason=reason,
        description=text,
        snapshot=snapshot,
        created_at=now,
        updated_at=now,
    )
    audit.record(actor, "report.submitted", "report", row.id)
    return row


def report_listing(actor: Actor, listing_id, reason, description=None) -> OwnReport:
    """Report a publicly visible Listing of another account.

    Visitor `unauthenticated`; an account without the `report` capability (Suspended,
    Deactivated, administrator) `forbidden`; bad values `FieldsInvalid`; a Listing that is not
    public `not_found`; your own `forbidden`; one already open `conflict`; more than the daily
    limit `throttled`. Nothing about the Listing changes."""
    _require_reporter(actor)
    errors, text = _clean_report(reason, description)
    if errors:
        raise FieldsInvalid(errors)
    with transaction.atomic():
        _lock_reporter(actor)
        snapshot = _snapshot(actor, listing_id)  # `not_found` unless public
        key = listings.canonical_uuid(listing_id)
        owner = listings.public_owner_id(Actor.system(actor.request_id), key)
        if owner == actor.account_id:
            raise DomainError("forbidden")
        row = _store(actor, _LISTING, key, owner, reason, text, snapshot)
    return _own(row)


def _selection(message_ids) -> tuple[list[uuid.UUID], str | None]:
    if message_ids is None or message_ids == []:
        return [], "required"
    if not isinstance(message_ids, list):
        return [], "invalid"
    if len(message_ids) > SELECTED_MAX:
        return [], "too_many_values"
    keys = [identity.parse_id(value) for value in message_ids]
    if any(key is None for key in keys):
        return [], "invalid"
    return list(dict.fromkeys(keys)), None


def report_conversation(
    actor: Actor, conversation_id, message_ids, reason, description=None
) -> OwnReport:
    """A participant reports Messages the other participant sent (Story 9.5). The snapshot is
    taken now: those Messages and up to five before the earliest one.

    Same refusals as `report_listing`; a Conversation the caller is not in is `not_found`, and an
    id that is not one of the other participant's Messages in it is a `message_ids` field error."""
    _require_reporter(actor)
    errors, text = _clean_report(reason, description)
    keys, problem = _selection(message_ids)
    if problem:
        errors["message_ids"] = problem
    if errors:
        raise FieldsInvalid(errors)
    with transaction.atomic():
        _lock_reporter(actor)
        try:
            evidence = messaging.report_evidence(actor, conversation_id, keys)
        except messaging.InvalidSelection:
            raise FieldsInvalid({"message_ids": "invalid_choice"}) from None
        snapshot = {
            "listing_id": str(evidence.listing_id),
            "messages": [
                {
                    "seq": m.seq,
                    "sender": "reporter" if m.from_reporter else "reported",
                    "selected": m.selected,
                    "body": m.body,
                    "sent_at": m.created_at.isoformat(),
                }
                for m in evidence.messages
            ],
        }
        row = _store(
            actor,
            _CONVERSATION,
            evidence.conversation_id,
            evidence.other_account_id,
            reason,
            text,
            snapshot,
        )
        # The other account may already be gone.
        if identity.participant(evidence.other_account_id).deactivated:
            _anonymise(row, evidence.other_account_id)
    return _own(row)


def _scrub(text: str) -> str:
    """The text with every phone number, email and address replaced."""
    merged: list[list[int]] = []
    for finding in find_contact_details(text):  # ordered by start; spans may overlap
        if merged and finding.start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], finding.end)
        else:
            merged.append([finding.start, finding.end])
    for start, end in reversed(merged):
        text = text[:start] + _REMOVED + text[end:]
    return text


def _anonymise(row: Report, account_id) -> bool:
    """Remove a deactivated participant from one Conversation snapshot: their side becomes
    "deleted user" and contact details are removed from all of its text. True when changed."""
    role = "reporter" if row.reporter_account_id == account_id else "reported"
    done = row.snapshot.get("anonymised", [])
    if role in done:
        return False
    messages = [
        {
            **m,
            "sender": DELETED_SENDER if m["sender"] == role else m["sender"],
            "body": _scrub(m["body"]),
        }
        for m in row.snapshot["messages"]
    ]
    row.snapshot = {**row.snapshot, "messages": messages, "anonymised": [*done, role]}
    row.description = _scrub(row.description)
    row.save(update_fields=["snapshot", "description"])
    audit.record(_SYSTEM, "report.snapshot_anonymised", "report", row.id)
    return True


def anonymise_participant(account_id) -> int:
    """Anonymise every Conversation snapshot this account takes part in. Safe to repeat."""
    with transaction.atomic():
        rows = (
            Report.objects.select_for_update()
            .filter(target_type=_CONVERSATION)
            .filter(Q(reporter_account_id=account_id) | Q(subject_account_id=account_id))
            .order_by("id")
        )
        return sum(_anonymise(row, account_id) for row in rows)


def purge_deactivated_snapshots(now: datetime, dry_run: bool = False) -> int:
    """Retention job: repair what the deactivation subscriber missed."""
    pending: dict[uuid.UUID, int] = {}
    for reporter, subject, snapshot in Report.objects.filter(target_type=_CONVERSATION).values_list(
        "reporter_account_id", "subject_account_id", "snapshot"
    ):
        done = snapshot.get("anonymised", [])
        for role, account_id in (("reporter", reporter), ("reported", subject)):
            if role not in done:
                pending[account_id] = pending.get(account_id, 0) + 1
    gone = [key for key, who in identity.participants(pending).items() if who.deactivated]
    if dry_run:
        return sum(pending[key] for key in gone)
    return sum(anonymise_participant(key) for key in gone)


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
    """Reports in one status, oldest first, with the unresolved counts. A row carries no Message
    text or description."""
    require_admin(actor)
    if status not in STATUSES or (reason is not None and reason not in REASONS):
        raise DomainError("validation_error")
    everything = Report.objects.all()
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
        [
            ReportRow(
                r.id, r.target_type, _listing_id(r), _title(r), r.reason, r.status, r.created_at
            )
            for r in items
        ],
        str(items[-1].id) if len(page) > PAGE_SIZE else None,
        counts.get("pending", 0),
        counts.get("reviewing", 0),
    )


def open_report_count(actor: Actor, listing_id) -> int:
    require_admin(actor)
    return Report.objects.filter(
        target_type=_LISTING, target_id=listing_id, status__in=OPEN_REPORT_STATUSES
    ).count()


def _report(report_id, *, lock: bool = False) -> Report:
    key = identity.parse_id(report_id)
    rows = Report.objects.select_for_update() if lock else Report.objects
    row = rows.filter(id=key).first() if key else None
    if row is None:
        raise DomainError("not_found")
    return row


def conversation_report_id(actor: Actor, report_id) -> uuid.UUID:
    """The id of an existing Conversation Report, else `not_found`; reads nothing else."""
    require_admin(actor)
    key = identity.parse_id(report_id)
    rows = Report.objects.filter(id=key, target_type=_CONVERSATION)
    if key is None or not rows.exists():
        raise DomainError("not_found")
    return key


def report_detail(actor: Actor, report_id) -> ReportDetail:
    """One Report. A Listing snapshot is public content, so it is included; a Conversation
    Report's Messages and description are left out (reasoned read)."""
    require_admin(actor)
    row = _report(report_id)
    public = row.target_type == _LISTING
    contacts = identity.masked_contacts([row.reporter_account_id, row.subject_account_id])
    return ReportDetail(
        id=row.id,
        target_type=row.target_type,
        listing_id=_listing_id(row),
        conversation_id=_conversation_id(row),
        reason=row.reason,
        status=row.status,
        description=row.description if public else "",
        snapshot=row.snapshot if public else None,
        messages=None,
        created_at=row.created_at,
        closed_at=row.closed_at,
        reporter=contacts.get(row.reporter_account_id),
        owner=contacts.get(row.subject_account_id),
        owner_account_id=row.subject_account_id,
        history=[
            Move(t.to_status, t.notes, t.created_at, t.actor_account_id)
            for t in row.transitions.order_by("created_at", "id")
        ],
        open_for_listing=Report.objects.filter(
            target_type=row.target_type,
            target_id=row.target_id,
            status__in=OPEN_REPORT_STATUSES,
        ).count(),
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
        row = _report(report_id, lock=True)
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
