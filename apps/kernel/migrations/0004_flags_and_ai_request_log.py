import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("kernel", "0003_idempotency_record"),
    ]

    operations = [
        migrations.CreateModel(
            name="FeatureFlag",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("name", models.CharField(max_length=64, unique=True)),
                ("enabled", models.BooleanField()),
                ("changed_by", models.UUIDField(blank=True, null=True)),
                ("changed_at", models.DateTimeField()),
            ],
        ),
        migrations.CreateModel(
            name="AiRequestLog",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("feature", models.CharField(max_length=48)),
                ("provider", models.CharField(max_length=48)),
                ("model", models.CharField(max_length=48)),
                ("latency_ms", models.PositiveIntegerField()),
                ("outcome", models.CharField(max_length=24)),
                ("created_at", models.DateTimeField()),
            ],
            options={
                "indexes": [models.Index(fields=["created_at"], name="ai_request_log_created_idx")],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "outcome__in",
                                (
                                    "success",
                                    "error",
                                    "timeout",
                                    "fallback",
                                    "budget_exhausted",
                                    "blocked",
                                ),
                            )
                        ),
                        name="ai_request_log_outcome_valid",
                    )
                ],
            },
        ),
    ]
