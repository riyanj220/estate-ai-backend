"""Agency Verification (Stories 8.3 and 8.4), Reports and moderation decisions (Epic 9).

Account and Listing ids are plain columns. The verified flag lives on the identity profile and is
changed only through `identity.services`; a Listing's moderation state lives in `listings` and is
changed only through `listings.moderation` and `listings.publication`.
"""

import uuid

from django.db import models

# What the Agency sees; `unverified` (no request) is derived, never stored.
REQUEST_STATUSES = ["pending", "verified", "rejected", "revoked", "withdrawn"]
DECISION_ACTIONS = ["approved", "rejected", "revoked", "withdrawn"]


class VerificationRequest(models.Model):
    """One request. `registration_number` and `note` are private: the owner, and administrators
    through a reasoned read."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agency_account_id = models.UUIDField()
    business_name = models.CharField(max_length=100)
    registration_number = models.CharField(max_length=60)
    note = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=16, default="pending")
    submitted_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.CharField(max_length=500, blank=True, default="")  # shown to owner

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=REQUEST_STATUSES),
                name="governance_verification_status_valid",
            ),
            models.UniqueConstraint(
                fields=["agency_account_id"],
                condition=models.Q(status="pending"),
                name="governance_verification_one_pending",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "submitted_at", "id"], name="governance_ver_queue_idx"),
            models.Index(fields=["agency_account_id"], name="governance_ver_agency_idx"),
        ]


class VerificationDecision(models.Model):
    """Append-only history (the runtime role cannot update or delete it)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        VerificationRequest, on_delete=models.CASCADE, related_name="decisions"
    )
    action = models.CharField(max_length=16)
    actor_account_id = models.UUIDField()  # the administrator, or the Agency for `withdrawn`
    reason = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=DECISION_ACTIONS),
                name="governance_decision_action_valid",
            ),
        ]


# `conversation` Reports arrive with Story 9.5; the shape already holds them.
REPORT_TARGETS = ["listing", "conversation"]
REPORT_REASONS = [
    "misleading",
    "duplicate",
    "prohibited_content",
    "abusive",
    "ai_generated_content",
    "other",
]
REPORT_STATUSES = ["pending", "reviewing", "resolved", "dismissed"]
OPEN_REPORT_STATUSES = ["pending", "reviewing"]


class Report(models.Model):
    """One user's Report. `snapshot` is the evidence at submission: for a Listing its public-view
    content only. The reporter is never shown to anyone but administrators."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    target_type = models.CharField(max_length=16, default="listing")
    target_id = models.UUIDField()  # the Listing or Conversation
    reporter_account_id = models.UUIDField()
    subject_account_id = models.UUIDField()  # the reported account (a Listing's owner)
    reason = models.CharField(max_length=24)
    description = models.CharField(max_length=1000, blank=True, default="")
    snapshot = models.JSONField()
    status = models.CharField(max_length=16, default="pending")
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target_type__in=REPORT_TARGETS),
                name="governance_report_target_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(reason__in=REPORT_REASONS),
                name="governance_report_reason_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=REPORT_STATUSES),
                name="governance_report_status_valid",
            ),
            models.UniqueConstraint(
                fields=["reporter_account_id", "target_type", "target_id"],
                condition=models.Q(status__in=OPEN_REPORT_STATUSES),
                name="governance_report_one_open",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at", "id"], name="governance_report_queue_idx"),
            models.Index(
                fields=["target_type", "target_id", "status"], name="governance_report_target_idx"
            ),
        ]


class ReportTransition(models.Model):
    """Append-only: each move of a Report with the administrator's notes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="transitions")
    to_status = models.CharField(max_length=16)
    actor_account_id = models.UUIDField()
    notes = models.CharField(max_length=1000, blank=True, default="")
    created_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(to_status__in=["reviewing", "resolved", "dismissed"]),
                name="governance_transition_status_valid",
            ),
        ]


class ModerationAction(models.Model):
    """Append-only: who did what to a Listing, why, when and with what outcome (Story 9.4)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing_id = models.UUIDField()
    action = models.CharField(max_length=24)
    actor_account_id = models.UUIDField()
    reason = models.CharField(max_length=200)
    status_before = models.CharField(max_length=16)
    status_after = models.CharField(max_length=16)
    restriction_after = models.BooleanField()
    created_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    action__in=[
                        "restrict",
                        "remove",
                        "lift_restriction",
                        "restore",
                        "archive_removed",
                    ]
                ),
                name="governance_moderation_action_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["listing_id", "-created_at"], name="governance_moderation_idx")
        ]
