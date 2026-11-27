"""Support requests (Story 10.9, FR-71).

Individual and Agency accounts write to the platform team, Suspended ones included, at most five
times a day (policy `support_requests`). An administrator sees the queue without the text, reads
one message only with a recorded reason (`selectors.admin_read_support_request`) and closes it;
the account is then notified. Account closure empties the messages (`erase_support`). Retention is
Story 10.12.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.governance.models import SUPPORT_CATEGORIES, SUPPORT_STATUSES, SupportRequest
from apps.governance.services import FieldsInvalid
from apps.identity import selectors as identity
from apps.identity.capabilities import Access, Capability, matrix_access
from apps.identity.policy import can
from apps.kernel import audit, notifications, ratelimit
from apps.kernel.actor import Actor, ActorKind, require_admin, require_signed_in
from apps.kernel.errors import DomainError

PAGE_SIZE = 20
CATEGORIES = tuple(SUPPORT_CATEGORIES)
STATUSES = tuple(SUPPORT_STATUSES)
MESSAGE_MIN, MESSAGE_MAX = 10, 2000
CLOSED_NOTIFICATION = "support.closed"
LIMIT_SCOPE = "support_requests"


class InvalidCursor(Exception):
    pass


@dataclass(frozen=True)
class OwnSupportRequest:
    id: uuid.UUID
    category: str
    message: str = field(repr=False)
    status: str
    submitted_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class QueueRow:
    """What an administrator sees without a reason: never the message."""

    id: uuid.UUID
    category: str
    status: str
    account_id: uuid.UUID
    account_type: str | None
    account_state: str | None
    masked_email: str | None  # None once the account is anonymised or gone
    submitted_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class QueuePage:
    items: list[QueueRow]
    next_cursor: str | None


def _own(row: SupportRequest) -> OwnSupportRequest:
    return OwnSupportRequest(
        row.id, row.category, row.message, row.status, row.submitted_at, row.closed_at
    )


def _require_requester(actor: Actor) -> None:
    """Individual and Agency accounts in any signed-in state (Suspended included)."""
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, Capability.REQUEST_SUPPORT):
        raise DomainError("forbidden")


def own_requests(actor: Actor) -> list[OwnSupportRequest]:
    """The caller's latest requests, newest first."""
    _require_requester(actor)
    rows = SupportRequest.objects.filter(account_id=actor.account_id).order_by(
        "-submitted_at", "-id"
    )
    return [_own(row) for row in rows[:PAGE_SIZE]]


def own_request(actor: Actor, request_id) -> OwnSupportRequest:
    """One of the caller's own; another account's, unknown and malformed ids are `not_found`."""
    _require_requester(actor)
    key = identity.parse_id(request_id)
    row = (
        SupportRequest.objects.filter(id=key, account_id=actor.account_id).first()
        if key is not None
        else None
    )
    if row is None:
        raise DomainError("not_found")
    return _own(row)


def _clean(category, message) -> tuple[str, str]:
    errors = {}
    if category not in CATEGORIES:
        errors["category"] = "required" if category in (None, "") else "invalid_choice"
    text = message.replace("\r\n", "\n").strip() if isinstance(message, str) else ""
    if message is None or (isinstance(message, str) and not text):
        errors["message"] = "required"
    elif not isinstance(message, str) or not text.replace("\n", "").replace("\t", "").isprintable():
        errors["message"] = "invalid"
    elif len(text) < MESSAGE_MIN:
        errors["message"] = "too_short"
    elif len(text) > MESSAGE_MAX:
        errors["message"] = "too_long"
    if errors:
        raise FieldsInvalid(errors)
    return category, text


def submit_request(actor: Actor, category, message) -> OwnSupportRequest:
    """Record a request; the sixth of a day is `throttled`."""
    _require_requester(actor)
    category, message = _clean(category, message)
    with transaction.atomic():
        # Locked so that a closure being approved cannot miss this request.
        account = identity.lock_account(actor.account_id)
        if matrix_access(account.state, Capability.REQUEST_SUPPORT) is not Access.ALLOWED:
            raise DomainError("forbidden")
        ratelimit.enforce(LIMIT_SCOPE, ratelimit.derive_key("account", str(account.id)))
        row = SupportRequest.objects.create(
            account_id=account.id,
            category=category,
            message=message,
            submitted_at=timezone.now(),
        )
        audit.record(actor, "support.submitted", "support_request", row.id)
    return _own(row)


def _rows(requests: list[SupportRequest]) -> list[QueueRow]:
    contacts = identity.masked_contacts([r.account_id for r in requests])
    out = []
    for row in requests:
        account = identity.get_account(row.account_id)
        state = account.state.value if account else None
        contact = contacts.get(row.account_id)
        out.append(
            QueueRow(
                row.id,
                row.category,
                row.status,
                row.account_id,
                contact.type if contact else None,
                state,
                contact.masked_email if contact and state != "deactivated" else None,
                row.submitted_at,
                row.closed_at,
            )
        )
    return out


def queue_row(actor: Actor, request_id: uuid.UUID) -> QueueRow:
    require_admin(actor)
    row = SupportRequest.objects.filter(id=request_id).first()
    if row is None:
        raise DomainError("not_found")
    return _rows([row])[0]


def review_queue(actor: Actor, status: str = "open", cursor: str | None = None) -> QueuePage:
    """Requests in one status, oldest first."""
    require_admin(actor)
    if status not in STATUSES:
        raise DomainError("validation_error")
    rows = SupportRequest.objects.filter(status=status).order_by("submitted_at", "id")
    if cursor is not None:
        key = identity.parse_id(cursor)
        last = SupportRequest.objects.filter(id=key).first() if key else None
        if last is None:
            raise InvalidCursor
        rows = rows.filter(
            Q(submitted_at__gt=last.submitted_at)
            | Q(submitted_at=last.submitted_at, id__gt=last.id)
        )
    page = list(rows[: PAGE_SIZE + 1])
    items = page[:PAGE_SIZE]
    return QueuePage(_rows(items), str(items[-1].id) if len(page) > PAGE_SIZE else None)


def close_request(actor: Actor, request_id) -> QueueRow:
    """Close an open request and tell its author. A closed one is `conflict`."""
    require_admin(actor)
    key = identity.parse_id(request_id)
    with transaction.atomic():
        row = SupportRequest.objects.select_for_update().filter(id=key).first() if key else None
        if row is None:
            raise DomainError("not_found")
        if row.status != "open":
            raise DomainError("conflict")
        row.status = "closed"
        row.closed_at = timezone.now()
        row.closed_by_account_id = actor.account_id
        row.save(update_fields=["status", "closed_at", "closed_by_account_id"])
        audit.record(actor, "support.closed", "support_request", row.id)
        view = _rows([row])[0]
        notifications.notify(row.account_id, CLOSED_NOTIFICATION, {})  # last: after commit
    return view


def erase_support(actor: Actor, account_id: uuid.UUID) -> None:
    """Account closure: empty every message and close what was open. The rows stay for
    retention (Story 10.12)."""
    require_admin(actor)
    mine = SupportRequest.objects.filter(account_id=account_id)
    mine.filter(status="open").update(
        status="closed", closed_at=timezone.now(), closed_by_account_id=actor.account_id
    )
    mine.update(message="")
