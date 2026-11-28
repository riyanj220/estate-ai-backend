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


class FeatureFlag(models.Model):
    """Database override of a flag declared in code (`kernel.flags.register_flag`)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64, unique=True)
    enabled = models.BooleanField()
    changed_by = models.UUIDField(null=True, blank=True)  # account id; null for the system
    changed_at = models.DateTimeField()


AI_OUTCOMES = ("success", "error", "timeout", "fallback", "budget_exhausted", "blocked")


class AiRequestLog(models.Model):
    """One AI call. Never holds content, account ids, prompts, responses or free text."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    feature = models.CharField(max_length=48)
    provider = models.CharField(max_length=48)
    model = models.CharField(max_length=48)
    latency_ms = models.PositiveIntegerField()
    outcome = models.CharField(max_length=24)
    created_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["created_at"], name="ai_request_log_created_idx")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(outcome__in=AI_OUTCOMES), name="ai_request_log_outcome_valid"
            )
        ]


class AiBudgetLedger(models.Model):
    """AI calls counted per provider and UTC day (AD-25). Written only by `kernel.ai.budget`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=48)
    day = models.DateField()
    used = models.PositiveIntegerField()
    open_used = models.PositiveIntegerField()  # the part used outside the reserved partition

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "day"], name="ai_budget_provider_day_uniq")
        ]


class AuditRecord(models.Model):
    """One security-relevant action. Append-only for the runtime role (never updated or purged).

    Holds plain UUID references and allow-listed metadata, never contact details or message text.
    `reason` is used only by reasoned private reads.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_id = models.UUIDField(null=True, blank=True)  # null for visitor and system actors
    actor_kind = models.CharField(max_length=16)
    action = models.CharField(max_length=64)
    target_type = models.CharField(max_length=64)
    target_id = models.UUIDField()
    reason = models.CharField(max_length=300, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["target_type", "target_id"], name="audit_target_idx"),
            models.Index(fields=["actor_id"], name="audit_actor_idx"),
            models.Index(fields=["created_at"], name="audit_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(actor_kind__in=["visitor", "user", "admin", "system"]),
                name="audit_actor_kind_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(actor_kind__in=["visitor", "system"], actor_id__isnull=True)
                    | models.Q(actor_kind__in=["user", "admin"], actor_id__isnull=False)
                ),
                name="audit_actor_id_matches_kind",
            ),
        ]


class Notification(models.Model):
    """One in-app notification for an account (Story 1.24). Written only by `kernel.notifications`.

    `seq` is allocated by the column default (a database sequence) in the INSERT and orders the
    list and its cursor (a UUID is not time-ordered). It is never returned as a field, but the
    cursor carries its position, so it is opaque rather than secret. `payload` is schema-checked
    public-class data.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seq = models.BigIntegerField(
        unique=True,
        editable=False,
        db_default=models.expressions.RawSQL("nextval('kernel_notification_seq')", []),
    )
    account_id = models.UUIDField()  # plain UUID: kernel never points at another app's table
    kind = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["account_id", "-seq"], name="notification_account_seq_idx"),
            models.Index(fields=["account_id", "read_at"], name="notification_account_read_idx"),
            models.Index(fields=["created_at"], name="notification_created_idx"),
            models.Index(fields=["read_at"], name="notification_read_at_idx"),
        ]


class MediaAsset(models.Model):
    """One stored, processed image (Story 2.9). Written only by `kernel.media`.

    Holds no file name and no original bytes: `storage_key` is a random name under id-only folders
    (see `kernel.storage`), and `bytes` is the
    size of the re-encoded file, which is what counts against the account's storage cap.
    `attached_at` is null until a feature (a Listing, a profile) takes the asset; assets still
    unattached after 24 hours are removed by retention.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_account_id = models.UUIDField()  # plain UUID: kernel never points at another app's table
    storage_key = models.CharField(max_length=200, unique=True)
    content_type = models.CharField(max_length=32)
    bytes = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    created_at = models.DateTimeField()
    attached_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner_account_id"], name="media_asset_owner_idx"),
            models.Index(
                fields=["created_at"],
                name="media_asset_unattached_idx",
                condition=models.Q(attached_at__isnull=True),
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(content_type__in=["image/jpeg", "image/png", "image/webp"]),
                name="media_asset_type_valid",
            ),
            models.CheckConstraint(condition=models.Q(bytes__gt=0), name="media_asset_bytes_gt_0"),
        ]


class StorageUsage(models.Model):
    """Bytes stored per account, locked (`select_for_update`) to check the cap atomically."""

    account_id = models.UUIDField(primary_key=True)
    bytes_used = models.BigIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(bytes_used__gte=0), name="storage_usage_bytes_gte_0"
            ),
        ]
