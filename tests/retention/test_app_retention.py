"""Retention of kernel and app-owned data and the audit purge (Stories 10.11 to 10.13).

The purge connection is stood in for by the test database's own connection here; the real purge
role is exercised in `test_purge_role.py`.
"""

import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from apps.assistant import retention as assistant_retention
from apps.assistant.models import AssistantSession, AssistantTurn
from apps.governance import retention as governance_retention
from apps.governance.models import (
    ClosureRequest,
    ModerationAction,
    Report,
    ReportTransition,
    SupportRequest,
    VerificationDecision,
    VerificationRequest,
)
from apps.kernel import audit, retention
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord
from apps.listings import publication
from apps.listings.models import PublicationEvent
from tests.listings.support import make_account

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=800)  # more than 24 months
RECENT = NOW - timedelta(days=400)  # more than 12 months, less than 24


@pytest.fixture
def purge_here(monkeypatch):
    monkeypatch.setattr(retention, "purge_alias", lambda: "default")


def check(job, expected):
    """A dry run reports the same count as the real run and deletes nothing."""
    assert job(NOW, True) == expected
    assert job(NOW, True) == expected
    assert job(NOW, False) == expected
    assert job(NOW, False) == 0


@pytest.mark.parametrize(
    ("now", "months", "expected"),
    [
        (datetime(2026, 11, 27, tzinfo=UTC), 24, datetime(2024, 11, 27, tzinfo=UTC)),
        (datetime(2028, 2, 29, tzinfo=UTC), 12, datetime(2027, 2, 28, tzinfo=UTC)),
        (datetime(2026, 1, 15, tzinfo=UTC), 1, datetime(2025, 12, 15, tzinfo=UTC)),
    ],
)
def test_months_ago(now, months, expected):
    assert retention.months_ago(now, months) == expected


def test_without_a_purge_connection_only_real_deletions_fail(db):
    assert "purge" not in settings.DATABASES
    assert publication.purge_old_events(NOW, False) == 0  # nothing to do needs no role
    event(LONG_AGO)
    assert publication.purge_old_events(NOW, True) == 1
    with pytest.raises(retention.PurgeRoleNotConfigured):
        publication.purge_old_events(NOW, False)
    assert PublicationEvent.objects.count() == 1


def event(created_at):
    return PublicationEvent.objects.create(
        account_id=uuid.uuid4(),
        listing_id=uuid.uuid4(),
        kind="initial",
        counted=True,
        identity_fingerprint="f",
        created_at=created_at,
    )


def test_publication_events_go_after_24_months(db, purge_here):
    event(LONG_AGO)
    kept = event(RECENT)
    check(publication.purge_old_events, 1)
    assert list(PublicationEvent.objects.values_list("id", flat=True)) == [kept.id]


def report(status, closed_at):
    row = Report.objects.create(
        target_type="listing",
        target_id=uuid.uuid4(),
        reporter_account_id=uuid.uuid4(),
        subject_account_id=uuid.uuid4(),
        reason="other",
        snapshot={},
        status=status,
        created_at=LONG_AGO,
        updated_at=LONG_AGO,
        closed_at=closed_at,
    )
    ReportTransition.objects.create(
        report=row, to_status="reviewing", actor_account_id=uuid.uuid4(), created_at=LONG_AGO
    )
    return row


def test_reports_go_24_months_after_resolution_and_open_ones_stay(db, purge_here):
    report("resolved", LONG_AGO)
    report("dismissed", LONG_AGO)
    kept = {report("resolved", RECENT).id, report("pending", None).id}
    kept.add(report("reviewing", None).id)
    check(governance_retention.reports, 2)
    assert set(Report.objects.values_list("id", flat=True)) == kept
    assert set(ReportTransition.objects.values_list("report_id", flat=True)) == kept


def verification_request(status, decided_at):
    row = VerificationRequest.objects.create(
        agency_account_id=uuid.uuid4(),
        business_name="Probe Estates",
        registration_number="REG-1",
        note="note",
        status=status,
        submitted_at=LONG_AGO,
        decided_at=decided_at,
    )
    if decided_at:
        VerificationDecision.objects.create(
            request=row, action="approved", actor_account_id=uuid.uuid4(), created_at=decided_at
        )
    return row


def test_verification_evidence_goes_24_months_after_the_decision(db, purge_here):
    for status in ("rejected", "revoked", "withdrawn"):
        verification_request(status, LONG_AGO)
    in_force = verification_request("verified", LONG_AGO)
    recent = verification_request("rejected", RECENT)
    pending = verification_request("pending", None)
    check(governance_retention.verification, 4)
    assert set(VerificationRequest.objects.values_list("id", flat=True)) == {
        in_force.id,
        recent.id,
        pending.id,
    }
    in_force.refresh_from_db()
    assert (in_force.status, in_force.registration_number, in_force.note) == ("verified", "", "")
    assert in_force.decisions.count() == 1
    recent.refresh_from_db()
    assert recent.registration_number == "REG-1"


def test_moderation_records_go_after_24_months(db, purge_here):
    for created in (LONG_AGO, RECENT):
        ModerationAction.objects.create(
            listing_id=uuid.uuid4(),
            action="restrict",
            actor_account_id=uuid.uuid4(),
            reason="reason",
            status_before="published",
            status_after="published",
            restriction_after=True,
            created_at=created,
        )
    check(governance_retention.moderation_records, 1)
    assert ModerationAction.objects.get().created_at == RECENT


def test_support_requests_go_12_months_after_closure_and_open_ones_stay(db):
    def support(status, closed_at):
        return SupportRequest.objects.create(
            account_id=uuid.uuid4(),
            category="other",
            message="a message long enough",
            status=status,
            submitted_at=LONG_AGO,
            closed_at=closed_at,
        )

    support("closed", RECENT)
    kept = {support("closed", NOW - timedelta(days=300)).id, support("open", None).id}
    check(governance_retention.support_requests, 1)
    assert set(SupportRequest.objects.values_list("id", flat=True)) == kept


def test_decided_closure_requests_go_after_24_months(db):
    def closure(status, decided_at):
        return ClosureRequest.objects.create(
            account_id=uuid.uuid4(),
            kind="deletion",
            status=status,
            submitted_at=LONG_AGO,
            decided_at=decided_at,
        )

    closure("approved", LONG_AGO)
    closure("rejected", LONG_AGO)
    kept = {closure("approved", RECENT).id, closure("pending", None).id}
    check(governance_retention.closure_requests, 2)
    assert set(ClosureRequest.objects.values_list("id", flat=True)) == kept


def test_assistant_history_goes_90_days_after_first_publication(db):
    owner = make_account()

    def session(first_published_at):
        from apps.listings.models import PropertyListing

        listing = PropertyListing.objects.create(
            owner_account_id=owner.id,
            purpose="sale",
            category="house",
            subtype="house",
            registry_version=1,
            status="published" if first_published_at else "draft",
            first_published_at=first_published_at,
        )
        row = AssistantSession.objects.create(
            account_id=owner.id,
            listing_id=listing.id,
            registry_version=1,
            schema_version=1,
            created_at=LONG_AGO,
        )
        AssistantTurn.objects.create(
            session=row,
            sequence=1,
            client_turn_id=uuid.uuid4(),
            user_text="text",
            created_at=LONG_AGO,
        )
        return row

    session(NOW - timedelta(days=91))
    kept = {session(NOW - timedelta(days=89)).id, session(None).id}
    check(assistant_retention.purge_published, 2)  # the session and its turn
    assert set(AssistantSession.objects.values_list("id", flat=True)) == kept


def audit_record(created_at):
    audit.register_action("probe.happened", {})
    with transaction.atomic():
        row = audit.record(Actor.system("probe"), "probe.happened", "probe", uuid.uuid4())
    AuditRecord.objects.filter(id=row.id).update(created_at=created_at)  # the test role may
    return row


def purge_command(*args):
    out = io.StringIO()
    call_command("purge_audit_records", *args, stdout=out)
    return out.getvalue().strip()


def test_audit_purge_dry_run_reports_and_deletes_nothing(db):
    audit_record(timezone.now() - timedelta(days=800))
    audit_record(timezone.now() - timedelta(days=700))
    assert purge_command("--dry-run") == "audit records older than 24 months: would delete 1"
    assert AuditRecord.objects.count() == 2


def test_audit_purge_deletes_only_old_records(db, purge_here):
    audit_record(timezone.now() - timedelta(days=800))
    kept = audit_record(timezone.now() - timedelta(days=700))
    assert purge_command() == "audit records older than 24 months: deleted 1"
    assert list(AuditRecord.objects.values_list("id", flat=True)) == [kept.id]


def test_audit_purge_without_the_role_deletes_nothing(db):
    audit_record(timezone.now() - timedelta(days=800))
    with pytest.raises(CommandError, match="PURGE_PASSWORD"):
        purge_command()
    assert AuditRecord.objects.count() == 1


def test_run_retention_never_touches_the_audit_trail(db, purge_here):
    audit_record(timezone.now() - timedelta(days=800))
    call_command("run_retention", stdout=io.StringIO())
    assert AuditRecord.objects.count() == 1
