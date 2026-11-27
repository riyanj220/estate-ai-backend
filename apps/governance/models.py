"""Agency Verification requests and their decision history (Stories 8.3 and 8.4).

`agency_account_id` and `actor_account_id` are plain ids. The verified flag itself lives on the
identity profile and is changed only through `identity.services`.
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
