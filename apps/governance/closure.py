"""Account closure requests (Story 10.1) and their decision and execution (Story 10.2, AD-23).

An approval is ONE transaction: the account is locked, then the request; identity deactivates it
(ending its sessions and emitting `account.session_ended`); every registered
`AccountDataEraser` then removes its app's share in layer order. Any failure leaves the account
Active and the request pending. The email is queued last and goes out after commit.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.governance import notices
from apps.governance.models import CLOSURE_KINDS, CLOSURE_STATUSES, ClosureRequest
from apps.governance.services import FieldsInvalid, _clean_reason
from apps.identity import selectors as identity
from apps.identity import services as identity_services
from apps.identity.capabilities import Access, Capability, matrix_access
from apps.identity.policy import can
from apps.kernel import audit, erasure, notifications
from apps.kernel.actor import Actor, ActorKind, require_admin, require_signed_in
from apps.kernel.errors import DomainError

PAGE_SIZE = 20
KINDS = tuple(CLOSURE_KINDS)
STATUSES = tuple(CLOSURE_STATUSES)
REJECTED_NOTIFICATION = "account.closure_rejected"


class InvalidCursor(Exception):
    pass


@dataclass(frozen=True)
class OwnClosureRequest:
    id: uuid.UUID
    kind: str
    status: str
    submitted_at: datetime
    decided_at: datetime | None
    decision_reason: str


@dataclass(frozen=True)
class OwnClosure:
    can_submit: bool
    request: OwnClosureRequest | None  # the latest one


@dataclass(frozen=True)
class QueueRow:
    id: uuid.UUID
    kind: str
    status: str
    account_id: uuid.UUID
    account_type: str | None
    account_state: str | None
    masked_email: str | None  # None once the account is anonymised or gone
    submitted_at: datetime
    decided_at: datetime | None
    decision_reason: str


@dataclass(frozen=True)
class QueuePage:
    items: list[QueueRow]
    next_cursor: str | None


def _own(row: ClosureRequest) -> OwnClosureRequest:
    return OwnClosureRequest(
        row.id, row.kind, row.status, row.submitted_at, row.decided_at, row.decision_reason
    )


def _require_requester(actor: Actor) -> None:
    """Individual and Agency accounts only, in any signed-in state (Suspended included)."""
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, Capability.REQUEST_DEACTIVATION):
        raise DomainError("forbidden")


def own_closure(actor: Actor) -> OwnClosure:
    _require_requester(actor)
    row = (
        ClosureRequest.objects.filter(account_id=actor.account_id)
        .order_by("-submitted_at", "-id")
        .first()
    )
    return OwnClosure(row is None or row.status != "pending", _own(row) if row else None)


def own_closure_request(actor: Actor, request_id) -> OwnClosureRequest:
    """One of the caller's own requests; another account's, unknown and malformed ids are
    `not_found`."""
    _require_requester(actor)
    key = identity.parse_id(request_id)
    row = (
        ClosureRequest.objects.filter(id=key, account_id=actor.account_id).first()
        if key is not None
        else None
    )
    if row is None:
        raise DomainError("not_found")
    return _own(row)


def request_closure(actor: Actor, kind) -> OwnClosure:
    """Record a request; the account stays as it is. `conflict` while one is pending."""
    _require_requester(actor)
    if kind not in KINDS:
        raise FieldsInvalid({"kind": "required" if kind in (None, "") else "invalid_choice"})
    with transaction.atomic():
        account = identity.lock_account(actor.account_id)
        if matrix_access(account.state, Capability.REQUEST_DEACTIVATION) is not Access.ALLOWED:
            raise DomainError("forbidden")
        if ClosureRequest.objects.filter(account_id=account.id, status="pending").exists():
            raise DomainError("conflict")
        row = ClosureRequest.objects.create(
            account_id=account.id, kind=kind, submitted_at=timezone.now()
        )
        audit.record(actor, "closure.requested", "account", account.id, {"kind": kind})
    return OwnClosure(False, _own(row))


def _rows(requests: list[ClosureRequest]) -> list[QueueRow]:
    ids = [r.account_id for r in requests]
    contacts = identity.masked_contacts(ids)
    out = []
    for row in requests:
        account = identity.get_account(row.account_id)
        state = account.state.value if account else None
        contact = contacts.get(row.account_id)
        out.append(
            QueueRow(
                row.id,
                row.kind,
                row.status,
                row.account_id,
                contact.type if contact else None,
                state,
                contact.masked_email if contact and state != "deactivated" else None,
                row.submitted_at,
                row.decided_at,
                row.decision_reason,
            )
        )
    return out


def review_queue(actor: Actor, status: str = "pending", cursor: str | None = None) -> QueuePage:
    """Requests in one status, oldest first."""
    require_admin(actor)
    if status not in STATUSES:
        raise DomainError("validation_error")
    rows = ClosureRequest.objects.filter(status=status).order_by("submitted_at", "id")
    if cursor is not None:
        key = identity.parse_id(cursor)
        last = ClosureRequest.objects.filter(id=key).first() if key else None
        if last is None:
            raise InvalidCursor
        rows = rows.filter(
            Q(submitted_at__gt=last.submitted_at)
            | Q(submitted_at=last.submitted_at, id__gt=last.id)
        )
    page = list(rows[: PAGE_SIZE + 1])
    items = page[:PAGE_SIZE]
    return QueuePage(_rows(items), str(items[-1].id) if len(page) > PAGE_SIZE else None)


def requests_of(actor: Actor, account_id: uuid.UUID) -> list[QueueRow]:
    """One account's closure requests, newest first (Story 10.4)."""
    require_admin(actor)
    rows = ClosureRequest.objects.filter(account_id=account_id).order_by("-submitted_at", "-id")
    return _rows(list(rows[:PAGE_SIZE]))


def _locked_pending(request_id):
    """Inside a transaction: the account, then the request, locked. Unknown ids are
    `not_found`; a decided request is `conflict`."""
    key = identity.parse_id(request_id)
    account_id = (
        ClosureRequest.objects.filter(id=key).values_list("account_id", flat=True).first()
        if key
        else None
    )
    if account_id is None:
        raise DomainError("not_found")
    account = identity.lock_account(account_id)
    request = ClosureRequest.objects.select_for_update().get(id=key)
    if request.status != "pending":
        raise DomainError("conflict")
    return account, request


def _decide(request: ClosureRequest, actor: Actor, status: str, reason: str) -> None:
    request.status = status
    request.decided_at = timezone.now()
    request.decided_by_account_id = actor.account_id
    request.decision_reason = reason
    request.save(update_fields=["status", "decided_at", "decided_by_account_id", "decision_reason"])
    audit.record(actor, f"closure.{status}", "account", request.account_id, {"kind": request.kind})


def approve_closure(actor: Actor, request_id, reason) -> QueueRow:
    """Deactivate and erase the account. A Suspended account can be closed; one that is already
    Deactivated is `conflict`."""
    require_admin(actor)
    reason = _clean_reason(reason)
    with transaction.atomic():
        account, request = _locked_pending(request_id)
        if account.state is identity.AccountState.DEACTIVATED:
            raise DomainError("conflict")
        address = identity.email_for_notice(actor, account.id)  # before it is anonymised
        _decide(request, actor, "approved", reason)
        identity_services.deactivate(actor, account.id)
        erasure.erase_all(actor, account.id)
        row = _rows([request])[0]
        notices.send_closure_approved(address, reason)  # last: after commit
    return row


def reject_closure(actor: Actor, request_id, reason) -> QueueRow:
    """The account stays as it is; it is told in-app and by email."""
    require_admin(actor)
    reason = _clean_reason(reason)
    with transaction.atomic():
        account, request = _locked_pending(request_id)
        address = identity.email_for_notice(actor, account.id)
        _decide(request, actor, "rejected", reason)
        row = _rows([request])[0]
        notifications.notify(account.id, REJECTED_NOTIFICATION, {})
        notices.send_closure_rejected(address, reason)  # last: after commit
    return row
