import uuid

from django.db import models
from django.db.models.functions import Length
from django.db.models.lookups import LessThanOrEqual
from django.utils import timezone

MAX_BODY = 2000


class Conversation(models.Model):
    """One account's private thread with the owner of one Listing (FR-48). Written only by
    `messaging.services`.

    `listing_id` and the account ids are plain columns (other apps' tables): the history is kept
    whatever later happens to the Listing, and what the header shows is decided when it is read.
    `last_seq` is the number of the newest Message; each `*_read_seq` is the last number that
    participant has read (unread state, Story 7.5)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing_id = models.UUIDField()
    initiator_account_id = models.UUIDField()
    owner_account_id = models.UUIDField()
    last_seq = models.PositiveIntegerField(default=0)
    initiator_read_seq = models.PositiveIntegerField(default=0)
    owner_read_seq = models.PositiveIntegerField(default=0)
    last_message_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["listing_id", "initiator_account_id"],
                name="messaging_one_per_listing_and_initiator",
            ),
            models.CheckConstraint(
                condition=~models.Q(initiator_account_id=models.F("owner_account_id")),
                name="messaging_not_with_oneself",
            ),
        ]
        indexes = [  # each participant's inbox, latest activity first
            models.Index(
                fields=["initiator_account_id", "-last_message_at"],
                name="messaging_initiator_idx",
            ),
            models.Index(
                fields=["owner_account_id", "-last_message_at"], name="messaging_owner_idx"
            ),
        ]


class Message(models.Model):
    """One text Message. `seq` orders the thread and is what a client resumes from (AD-13);
    `client_message_id` is the sender's `Idempotency-Key`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    seq = models.PositiveIntegerField()
    sender_account_id = models.UUIDField()
    body = models.TextField()
    client_message_id = models.CharField(max_length=128)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "seq"], name="messaging_message_seq_uniq"
            ),
            models.UniqueConstraint(
                fields=["conversation", "sender_account_id", "client_message_id"],
                name="messaging_message_client_id_uniq",
            ),
            models.CheckConstraint(
                condition=~models.Q(body="") & models.Q(LessThanOrEqual(Length("body"), MAX_BODY)),
                name="messaging_message_body_length",
            ),
        ]
