import uuid

from django.db import models


class AssistantSession(models.Model):
    """One conversation about one Draft (Story 5.8). Written only by `assistant.services`.

    `listing_id` has no foreign key (another app's table): deletion goes through
    `listing.deleted` and the retention job. At most one session per Listing is open."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_id = models.UUIDField()
    listing_id = models.UUIDField()
    registry_version = models.PositiveIntegerField()
    schema_version = models.PositiveIntegerField()  # of the extraction schema
    created_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["listing_id"], name="assistant_session_listing")]
        constraints = [
            models.UniqueConstraint(
                fields=["listing_id"],
                condition=models.Q(closed_at__isnull=True),
                name="assistant_one_open_session",
            ),
        ]


class AssistantTurn(models.Model):
    """One message and its reply, both REDACTED text only (AD-8). An empty `reply_text` marks a
    turn still being answered; a turn that fails is deleted."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(AssistantSession, on_delete=models.CASCADE, related_name="turns")
    sequence = models.PositiveIntegerField()
    client_turn_id = models.UUIDField()
    user_text = models.TextField()
    reply_text = models.TextField(blank=True, default="")
    sources = models.JSONField(default=list)  # [{title, source, version}] of retrieved passages
    # what the turn did to the Draft (Story 5.9): {applied, rejected, conflicts, notices}
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "sequence"], name="assistant_turn_sequence_uniq"
            ),
            models.UniqueConstraint(
                fields=["session", "client_turn_id"], name="assistant_turn_client_uniq"
            ),
        ]
