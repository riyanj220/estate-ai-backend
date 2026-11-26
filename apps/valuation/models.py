import uuid

from django.db import models


class ValuationModelVersion(models.Model):
    """A registered, trained model (AD-12). Written only by `valuation.services`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64)
    version = models.PositiveIntegerField()
    feature_schema_version = models.PositiveIntegerField()
    metrics = models.JSONField()
    trained_at = models.DateTimeField()
    artifact_sha256 = models.CharField(max_length=64)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["name", "version"], name="valuation_model_name_version_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(artifact_sha256__regex=r"^[0-9a-f]{64}$"),
                name="valuation_model_sha256_shape",
            ),
        ]
