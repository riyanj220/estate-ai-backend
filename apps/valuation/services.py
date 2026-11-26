"""Write path of `valuation`: registering a trained model (Story 4.2) and computing AI Price
Estimates (Story 4.3)."""

import logging
import math
import time
import uuid
from collections.abc import Mapping
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from apps.catalog import registry
from apps.catalog import selectors as catalog
from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.kernel import ai_log, runtime
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.listings import selectors as listings
from apps.listings import validation
from apps.listings.services import FieldsInvalid
from apps.valuation import features, model_store, selectors, supportability
from apps.valuation.data import localities
from apps.valuation.models import PriceEstimate, ValuationModelVersion

logger = logging.getLogger("estate.valuation")

# What a caller may enter; `subtype` comes with the taxonomy. Nothing else (no price, no address).
ATTRIBUTE_FIELDS = tuple(name for name in features.SOURCE_FIELDS if name != "subtype")
TIMEOUT_SECONDS = 20  # the first call imports scikit-learn; later ones take milliseconds
ROUND_TO = 1000  # PKR; the model is no more precise than this
AI_FEATURE = "price_estimate"


@dataclass(frozen=True)
class EstimateOutcome:
    """Either a value (`reason` None) or an unavailable reason and no number."""

    value: Decimal | None = None
    model_version: int | None = None
    estimated_at: datetime | None = None
    reason: str | None = None


def _require_requester(actor: Actor) -> None:
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, Capability.EDIT_DRAFTS):
        raise DomainError("forbidden")


def estimate_for_attributes(
    actor: Actor, purpose, category, subtype, values: object
) -> EstimateOutcome:
    """An estimate for entered attributes, validated by the Listing field rules.

    Visitor `unauthenticated`; Suspended, Deactivated and administrators `forbidden`; invalid
    values `validation_error` (all at once, codes only)."""
    _require_requester(actor)
    errors = {}
    if purpose not in registry.purposes():
        errors["purpose"] = "invalid_choice"
    if not isinstance(category, str) or not registry.subtypes_of(category):
        errors["category"] = "invalid_choice"
    elif not isinstance(subtype, str) or not registry.valid_subtype(category, subtype):
        errors["subtype"] = "subtype_not_in_category"
    if not isinstance(values, Mapping):
        errors["fields"] = "not_a_dict"
    elif set(values) - set(ATTRIBUTE_FIELDS):
        errors["fields"] = "unknown_field"
    if errors:
        raise FieldsInvalid(errors)
    chosen = catalog.get_locality(values.get("locality"))
    checked, errors = validation.evaluate(
        purpose,
        category,
        subtype,
        {"city": chosen.city_id if chosen else None},
        values,
        resolve=lambda _kind, key: found.city_id if (found := catalog.get_locality(key)) else None,
        amenity_codes=catalog.amenity_codes(),
    )
    if errors:
        raise FieldsInvalid(errors)
    wire = {name: listings.field_text(name, value) for name, value in checked.items()}
    wire |= {"purpose": purpose, "category": category, "subtype": subtype}
    wire["city"] = chosen.city_id if chosen else None
    return _estimate(actor, wire, listing_id=None)


def estimate_for_draft(actor: Actor, listing_id) -> EstimateOutcome:
    """An estimate for the account's own Draft.

    Visitor `unauthenticated`; not the owner's (also unknown and malformed) `not_found`;
    Suspended or Deactivated `forbidden`; a Listing that is not a Draft `conflict`."""
    require_signed_in(actor)
    view = listings.get_owned_listing(actor, listing_id)
    _require_requester(actor)
    if view.status != "draft":
        raise DomainError("conflict")
    return _estimate(actor, view.fields, listing_id=view.id)


def _name_of(names, value) -> str | None:
    key = catalog.parse_uuid(value)
    return None if key is None else names([key]).get(key)


def _estimate(actor: Actor, fields: Mapping, listing_id: uuid.UUID | None) -> EstimateOutcome:
    locality = _name_of(catalog.locality_names, fields.get("locality"))
    reason = supportability.classify(
        purpose=fields.get("purpose"),
        category=fields.get("category"),
        subtype=fields.get("subtype"),
        city=_name_of(catalog.city_names, fields.get("city")),
        locality=locality,
        area_value=fields.get("area_value"),
        area_unit=fields.get("area_unit"),
        marla_basis=fields.get("marla_basis"),
        covered=localities.load().base_rates,
    )
    if reason:
        return EstimateOutcome(reason=reason)
    inputs = features.snapshot_of(fields, locality)
    model = selectors.current_model()
    value, reason = _run_model(model, inputs)
    if reason:
        return EstimateOutcome(reason=reason)
    row = PriceEstimate.objects.create(
        account_id=actor.account_id,
        listing_id=listing_id,
        model_version_id=model.id,
        feature_schema_version=features.FEATURE_SCHEMA_VERSION,
        inputs=inputs,
        inputs_sha256=features.snapshot_hash(inputs),
        value=value,
        created_at=timezone.now(),
    )
    return EstimateOutcome(row.value, model.version, row.created_at)


def _infer(model: selectors.ModelVersionRef, inputs: dict) -> float | None:
    """On a worker thread; no database. None: the model does not cover these inputs."""
    loaded = selectors.load_model(model)
    try:
        return loaded.estimate(features.build_features(inputs))
    except features.UnsupportedInput:
        return None


def _run_model(model, inputs: dict) -> tuple[Decimal | None, str | None]:
    """(value, None) or (None, reason). Every attempt is in the AI request log, without content."""
    started = time.monotonic()
    value, reason, outcome = None, "temporarily_unavailable", "error"
    try:
        if model is not None:
            future = runtime.submit_heavy(_infer, model, inputs)
            try:
                raw = future.result(timeout=TIMEOUT_SECONDS)
            except FutureTimeout:
                future.cancel()
                outcome = "timeout"
            else:
                if raw is None:
                    reason, outcome = "locality_not_covered", "blocked"
                elif math.isfinite(raw) and raw >= ROUND_TO:
                    value = Decimal(round(raw / ROUND_TO) * ROUND_TO).quantize(Decimal("0.01"))
                    reason, outcome = None, "success"
    except runtime.ExecutorBusy:
        outcome = "fallback"
    except Exception as exc:  # a failed computation is an unavailable answer, never a 500
        logger.error(
            "estimate_failed",
            extra={"event": "estimate_failed", "exception_class": type(exc).__name__},
        )
    _log_call(model, round((time.monotonic() - started) * 1000), outcome)
    return value, reason


def _log_call(model, latency_ms: int, outcome: str) -> None:
    name = f"{model.name}-{model.version}" if model else "none"
    try:
        with transaction.atomic():
            ai_log.record_ai_call(
                AI_FEATURE, "local", ai_log.normalise_token(name), latency_ms, outcome
            )
    except Exception as exc:  # logging never fails the request
        logger.warning(
            "ai_log_failed",
            extra={"event": "ai_log_failed", "exception_class": type(exc).__name__},
        )


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
