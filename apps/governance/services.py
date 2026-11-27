"""Agency Verification (Stories 8.3 and 8.4). A light formality: no document is uploaded and
nothing is checked against an outside register; an administrator decides.

Every change locks the Agency account first (`identity.lock_account`), writes its audit record in
the same transaction and, for an administrator's decision, notifies the Agency last.
"""

import re
import uuid

from django.db import transaction
from django.utils import timezone

from apps.governance import selectors
from apps.governance.models import VerificationDecision, VerificationRequest
from apps.identity import selectors as identity
from apps.identity import services as identity_services
from apps.kernel import audit, notifications
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError
from apps.kernel.privacy.patterns import LONG_DIGIT_RUN, reject_contact_details

BUSINESS_NAME_MIN, BUSINESS_NAME_MAX = 2, 100
NUMBER_MIN, NUMBER_MAX = 3, 60
NOTE_MAX = 500
REASON_MIN, REASON_MAX = 5, 500
_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ./-]*")
# A Suspended Agency is read-only; a Deactivated one has no session.
_MAY_ACT = (identity.AccountState.ACTIVE, identity.AccountState.EMAIL_UNVERIFIED)


class FieldsInvalid(Exception):
    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("invalid fields")
        self.fields = fields


def _text(value, low: int, high: int) -> tuple[str, str | None]:
    """The value as one line with single spaces, and an error code or None."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        return "", "invalid"
    value = " ".join(value.split())
    if not value:
        return value, "required" if low else None
    if len(value) < low:
        return value, "too_short"
    if len(value) > high:
        return value, "too_long"
    if not value.isprintable():
        return value, "invalid"
    return value, None


def _clean_request(business_name, registration_number, note) -> dict[str, str]:
    values, errors = {}, {}
    for name, raw, low, high in (
        ("business_name", business_name, BUSINESS_NAME_MIN, BUSINESS_NAME_MAX),
        ("registration_number", registration_number, NUMBER_MIN, NUMBER_MAX),
        ("note", note, 0, NOTE_MAX),
    ):
        values[name], error = _text(raw, low, high)
        if error:
            errors[name] = error
    if "registration_number" not in errors and not _NUMBER.fullmatch(values["registration_number"]):
        errors["registration_number"] = "invalid"
    if errors:
        raise FieldsInvalid(errors)
    return values


def _clean_reason(reason) -> str:
    """The reason the Agency will read: one line, no contact details."""
    value, error = _text(reason, REASON_MIN, REASON_MAX)
    if not error and (LONG_DIGIT_RUN.search(value) or reject_contact_details(value) is not None):
        error = "contains_contact_details"
    if error:
        raise FieldsInvalid({"reason": error})
    return value


def _lock_own(actor: Actor) -> None:
    """Inside a transaction: the signed-in Agency's account, locked and still allowed to act."""
    if identity.lock_account(actor.account_id).state not in _MAY_ACT:
        raise DomainError("forbidden")


def _decide(request: VerificationRequest, actor: Actor, action: str, status: str, reason: str):
    now = timezone.now()
    request.status = status
    request.decided_at = now
    request.decision_reason = reason
    request.save(update_fields=["status", "decided_at", "decision_reason"])
    VerificationDecision.objects.create(
        request=request,
        action=action,
        actor_account_id=actor.account_id,
        reason=reason,
        created_at=now,
    )
    audit.record(actor, f"verification.{action}", "account", request.agency_account_id)


def submit_request(actor: Actor, business_name, registration_number, note=""):
    """A new pending request. `conflict` while one is pending or the Agency is already
    verified; `forbidden` for a Suspended Agency or another account type."""
    identity_services.require_agency(actor)
    values = _clean_request(business_name, registration_number, note)
    with transaction.atomic():
        _lock_own(actor)
        facts = identity.agency_facts([actor.account_id]).get(actor.account_id)
        if facts is None:
            raise DomainError("forbidden")
        pending = VerificationRequest.objects.filter(
            agency_account_id=actor.account_id, status="pending"
        )
        if facts.verified or pending.exists():
            raise DomainError("conflict")
        VerificationRequest.objects.create(
            agency_account_id=actor.account_id, submitted_at=timezone.now(), **values
        )
        audit.record(actor, "verification.requested", "account", actor.account_id)
    return selectors.own_verification(actor)


def withdraw_request(actor: Actor):
    """Withdraw the pending request; `conflict` when there is none."""
    identity_services.require_agency(actor)
    with transaction.atomic():
        _lock_own(actor)
        request = (
            VerificationRequest.objects.select_for_update()
            .filter(agency_account_id=actor.account_id, status="pending")
            .first()
        )
        if request is None:
            raise DomainError("conflict")
        _decide(request, actor, "withdrawn", "withdrawn", "")
    return selectors.own_verification(actor)


def _locked_request(request_id, expected: str):
    """Inside a transaction: the Agency account, then the request, locked. Unknown ids are
    `not_found`; a request in another status is `conflict`."""
    key = selectors.parse_request_id(request_id)
    found = VerificationRequest.objects.filter(id=key).values_list("agency_account_id", flat=True)
    account_id: uuid.UUID | None = found.first() if key else None
    if account_id is None:
        raise DomainError("not_found")
    account = identity.lock_account(account_id)
    request = VerificationRequest.objects.select_for_update().get(id=key)
    if request.status != expected:
        raise DomainError("conflict")
    return account, request


def approve_request(actor: Actor, request_id):
    """Verify the Agency. A Suspended or Deactivated Agency cannot be approved (`conflict`)."""
    require_admin(actor)
    with transaction.atomic():
        account, request = _locked_request(request_id, "pending")
        if account.state not in _MAY_ACT:
            raise DomainError("conflict")
        identity_services.set_agency_verification(actor, account.id, True)
        _decide(request, actor, "approved", "verified", "")
        notifications.notify(account.id, "verification.approved", {})  # last
    return request


def reject_request(actor: Actor, request_id, reason):
    require_admin(actor)
    reason = _clean_reason(reason)
    with transaction.atomic():
        account, request = _locked_request(request_id, "pending")
        _decide(request, actor, "rejected", "rejected", reason)
        notifications.notify(account.id, "verification.rejected", {})  # last
    return request


def revoke_verification(actor: Actor, request_id, reason):
    """Remove a previous approval. The Agency's Listings are untouched."""
    require_admin(actor)
    reason = _clean_reason(reason)
    with transaction.atomic():
        account, request = _locked_request(request_id, "verified")
        identity_services.set_agency_verification(actor, account.id, False)
        _decide(request, actor, "revoked", "revoked", reason)
        notifications.notify(account.id, "verification.revoked", {})  # last
    return request


_CLOSED_REASON = "The account was closed."


def erase_verification(actor: Actor, account_id) -> None:
    """Account closure: withdraw a pending request, revoke an approval (clearing the verified
    flag) and blank the submitted details of every request. The rows and their decision history
    stay. Inside the closure's transaction."""
    require_admin(actor)
    requests = VerificationRequest.objects.select_for_update().filter(agency_account_id=account_id)
    for request in requests.order_by("id"):
        if request.status == "pending":
            _decide(request, actor, "withdrawn", "withdrawn", _CLOSED_REASON)
        elif request.status == "verified":
            _decide(request, actor, "revoked", "revoked", _CLOSED_REASON)
    requests.update(
        business_name=identity_services.DELETED_AGENCY_NAME, registration_number="", note=""
    )
    if account_id in identity.agency_facts([account_id]):
        identity_services.set_agency_verification(actor, account_id, False)
