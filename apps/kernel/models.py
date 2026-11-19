"""Kernel tables. References to other apps are plain UUIDs or strings, never foreign keys."""

import uuid

from django.db import models


class RateLimitPolicy(models.Model):
    """One row per limit (NFR-10). The only source of limits; edit rows to retune."""

    KEY_ACCOUNT = "account"
    KEY_CLIENT = "client"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=64, unique=True)
    limit = models.PositiveIntegerField()
    # Null for caps (for example favourites): the owning story counts against `limit`.
    window_seconds = models.PositiveIntegerField(null=True, blank=True)
    key_kind = models.CharField(
        max_length=16, choices=[(KEY_ACCOUNT, "account"), (KEY_CLIENT, "client")]
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(limit__gt=0), name="ratelimit_limit_gt_0"),
            models.CheckConstraint(
                condition=models.Q(window_seconds__isnull=True) | models.Q(window_seconds__gt=0),
                name="ratelimit_window_gt_0",
            ),
        ]


class RateLimitCounter(models.Model):
    """Fixed-window hit count. `key_hash` is an HMAC; no raw address or session is stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=64)
    key_hash = models.CharField(max_length=64)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["window_start"], name="ratelimit_counter_window_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "key_hash", "window_start"], name="ratelimit_counter_window_uniq"
            )
        ]


class IdempotencyRecord(models.Model):
    """A claimed idempotency key (AD-17). Holds a hash and a result reference, never a body."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=64)
    account_id = models.UUIDField()  # plain UUID: kernel never points at another app's table
    key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    result_ref = models.CharField(max_length=128, null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["created_at"], name="idempotency_created_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "account_id", "key"], name="idempotency_scope_account_key_uniq"
            )
        ]
