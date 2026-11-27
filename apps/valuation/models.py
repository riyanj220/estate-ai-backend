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


class PriceEstimate(models.Model):
    """One computed AI Price Estimate (Story 4.3). Written only by `valuation.services`.

    `inputs` is the non-private feature snapshot (`features.SOURCE_FIELDS`, the locality by name):
    `features.build_features(inputs)` and the recorded model version reproduce `value`.
    `listing_id` is the Listing it is for, null for entered attributes; there is no
    foreign key (another app's table), so deletion goes through `listing.deleted` and the
    retention job."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_id = models.UUIDField()
    listing_id = models.UUIDField(null=True)
    model_version = models.ForeignKey(ValuationModelVersion, on_delete=models.PROTECT)
    feature_schema_version = models.PositiveIntegerField()
    inputs = models.JSONField()
    inputs_sha256 = models.CharField(max_length=64)
    value = models.DecimalField(max_digits=14, decimal_places=2)  # PKR
    # the 5-Year Price Forecast of `value` (`forecasting.compute`); null on rows older than it
    forecast = models.JSONField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["listing_id"], name="valuation_estimate_listing"),
            models.Index(fields=["created_at"], name="valuation_estimate_created"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(value__gt=0), name="valuation_estimate_positive"
            ),
        ]
