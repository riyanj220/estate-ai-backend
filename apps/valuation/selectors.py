"""Read path of `valuation`. Returns plain immutable values, never model instances."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from apps.catalog import selectors as catalog
from apps.kernel.actor import Actor
from apps.listings import selectors as listings
from apps.valuation import features, model_store
from apps.valuation.models import PriceEstimate, ValuationModelVersion


@dataclass(frozen=True)
class ModelVersionRef:
    id: uuid.UUID
    name: str
    version: int
    feature_schema_version: int
    metrics: dict
    trained_at: datetime
    artifact_sha256: str


def current_model() -> ModelVersionRef | None:
    """The newest registered version that matches this code's feature schema."""
    row = (
        ValuationModelVersion.objects.filter(
            name=model_store.MODEL_NAME, feature_schema_version=features.FEATURE_SCHEMA_VERSION
        )
        .order_by("-version")
        .first()
    )
    if row is None:
        return None
    return ModelVersionRef(
        id=row.id,
        name=row.name,
        version=row.version,
        feature_schema_version=row.feature_schema_version,
        metrics=row.metrics,
        trained_at=row.trained_at,
        artifact_sha256=row.artifact_sha256,
    )


def load_model(
    ref: ModelVersionRef, directory: Path = model_store.ARTIFACTS
) -> model_store.LoadedModel:
    """The model of a registered version, hash-checked against what was registered."""
    return model_store.load(ref.version, ref.artifact_sha256, directory)


@dataclass(frozen=True)
class StoredEstimate:
    value: Decimal
    model_version: int
    estimated_at: datetime


def latest_draft_estimate(actor: Actor, listing_id) -> StoredEstimate | None:
    """The newest stored estimate of the account's own Listing that still matches its saved model
    inputs (Story 4.4); None when there is none or the inputs changed since. Computes nothing.

    Anything but the account's own Listing is `not_found`."""
    view = listings.get_owned_listing(actor, listing_id)
    key = catalog.parse_uuid(view.fields.get("locality"))
    locality = None if key is None else catalog.locality_names([key]).get(key)
    if locality is None:
        return None
    digest = features.snapshot_hash(features.snapshot_of(view.fields, locality))
    row = (
        PriceEstimate.objects.filter(account_id=actor.account_id, listing_id=view.id)
        .select_related("model_version")
        .order_by("-created_at")
        .first()
    )
    if row is None or row.inputs_sha256 != digest:
        return None
    return StoredEstimate(row.value, row.model_version.version, row.created_at)
