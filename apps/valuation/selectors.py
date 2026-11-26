"""Read path of `valuation`. Returns plain immutable values, never model instances."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from apps.valuation import features, model_store
from apps.valuation.models import ValuationModelVersion


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
