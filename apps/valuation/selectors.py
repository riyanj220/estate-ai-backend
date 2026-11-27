"""Read path of `valuation`. Returns plain immutable values, never model instances."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from apps.catalog import selectors as catalog
from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.kernel.actor import Actor, ActorKind
from apps.kernel.errors import DomainError
from apps.listings import selectors as listings
from apps.valuation import features, model_store, supportability
from apps.valuation.data import localities
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
    forecast: dict | None = None


@dataclass(frozen=True)
class EstimateOutcome:
    """Either a value (`reason` None) or an unavailable reason and no number."""

    value: Decimal | None = None
    model_version: int | None = None
    estimated_at: datetime | None = None
    reason: str | None = None
    forecast: dict | None = None  # only with a value


def unsupported_reason(fields: Mapping, locality: str | None) -> str | None:
    """Why these Listing fields cannot be estimated (`supportability`), or None. `locality` is
    the locality NAME."""
    return supportability.classify(
        purpose=fields.get("purpose"),
        category=fields.get("category"),
        subtype=fields.get("subtype"),
        city=name_of(catalog.city_names, fields.get("city")),
        locality=locality,
        area_value=fields.get("area_value"),
        area_unit=fields.get("area_unit"),
        marla_basis=fields.get("marla_basis"),
        covered=localities.load().base_rates,
    )


def name_of(names, value) -> str | None:
    key = catalog.parse_uuid(value)
    return None if key is None else names([key]).get(key)


def listing_fields(view: listings.PublicListingView) -> dict:
    """The public projection as estimate input: its fields with the taxonomy beside them."""
    return {
        **view.fields,
        "purpose": view.purpose,
        "category": view.category,
        "subtype": view.subtype,
    }


def stored_estimate(listing_id, inputs: Mapping) -> StoredEstimate | None:
    """The newest stored estimate of the Listing computed from exactly these inputs; an estimate
    of other inputs is stale and never returned. Computes nothing."""
    row = (
        PriceEstimate.objects.filter(
            listing_id=listing_id,
            inputs_sha256=features.snapshot_hash(inputs),
            feature_schema_version=features.FEATURE_SCHEMA_VERSION,
        )
        .select_related("model_version")
        .order_by("-created_at")
        .first()
    )
    if row is None:
        return None
    return StoredEstimate(row.value, row.model_version.version, row.created_at, row.forecast)


def current_estimate(actor: Actor, listing_id) -> EstimateOutcome:
    """The estimate of a publicly visible Listing for its saved model inputs (Story 4.5); anything
    else is `not_found`.

    A stored one is returned as it is. When there is none (never computed, or the inputs changed
    since) it is computed now for a signed-in account; for a Visitor it stays unavailable, so a
    Visitor never triggers computation."""
    view = listings.public_listing(actor, listing_id)
    fields = listing_fields(view)
    locality = name_of(catalog.locality_names, fields.get("locality"))
    reason = unsupported_reason(fields, locality)
    if reason:
        return EstimateOutcome(reason=reason)
    found = stored_estimate(view.id, features.snapshot_of(fields, locality))
    if found is not None:
        return EstimateOutcome(
            found.value, found.model_version, found.estimated_at, forecast=found.forecast
        )
    if actor.kind is not ActorKind.USER:
        return EstimateOutcome(reason="temporarily_unavailable")
    from apps.valuation import services  # the write path imports this module

    return services.recompute_public_listing(Actor.system("valuation"), view.id)


def can_recompute(actor: Actor, listing_id) -> bool:
    """Whether the actor may ask for this Listing's estimate again: its owner, in an account
    state that may edit."""
    if actor.kind is not ActorKind.USER or not can(actor, Capability.EDIT_DRAFTS):
        return False
    try:
        listings.get_owned_listing(actor, listing_id)
    except DomainError:
        return False
    return True


def latest_draft_estimate(actor: Actor, listing_id) -> StoredEstimate | None:
    """The newest stored estimate of the account's own Listing that still matches its saved model
    inputs (Story 4.4); None when there is none or the inputs changed since. Computes nothing.

    Anything but the account's own Listing is `not_found`."""
    view = listings.get_owned_listing(actor, listing_id)
    locality = name_of(catalog.locality_names, view.fields.get("locality"))
    if locality is None:
        return None
    return stored_estimate(view.id, features.snapshot_of(view.fields, locality))
