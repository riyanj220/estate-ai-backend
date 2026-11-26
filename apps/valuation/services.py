"""Write path of `valuation`: registering a trained model (Story 4.2)."""

from datetime import datetime
from pathlib import Path

from django.db import transaction

from apps.kernel.actor import Actor, ActorKind
from apps.kernel.errors import DomainError
from apps.valuation import features, model_store
from apps.valuation.models import ValuationModelVersion


class RegistrationRefused(Exception):
    """The artifact cannot be registered; the message is one plain line."""


def register_model(
    actor: Actor, version: int | None = None, directory: Path = model_store.ARTIFACTS
) -> tuple[ValuationModelVersion, bool]:
    """Register the artifact of `version` (default: the newest in `directory`) after checking
    its hash. Idempotent: (row, created). A version never changes its artifact.
    The system actor only."""
    if actor.kind is not ActorKind.SYSTEM:
        raise DomainError("forbidden")
    available = model_store.versions(directory)
    if version is None:
        if not available:
            raise RegistrationRefused("There is no trained model to register.")
        version = available[-1]
    try:
        manifest = model_store.read_manifest(version, directory)
        model_store.read_checked(version, manifest["artifact_sha256"], directory)
    except model_store.ArtifactError as error:
        raise RegistrationRefused(str(error)) from None
    if manifest["feature_schema_version"] != features.FEATURE_SCHEMA_VERSION:
        raise RegistrationRefused("The model was built for another feature schema; retrain.")
    with transaction.atomic():
        row, created = ValuationModelVersion.objects.get_or_create(
            name=manifest["name"],
            version=version,
            defaults={
                "feature_schema_version": manifest["feature_schema_version"],
                "metrics": manifest["metrics"],
                "trained_at": datetime.fromisoformat(manifest["trained_at"]),
                "artifact_sha256": manifest["artifact_sha256"],
            },
        )
    if row.artifact_sha256 != manifest["artifact_sha256"]:
        raise RegistrationRefused(
            f"Version {version} is already registered with another artifact; use a new version."
        )
    return row, created
