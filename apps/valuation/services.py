"""Write path of `valuation`: registering a trained model (Story 4.2) and computing AI Price
Estimates (Story 4.3)."""

import logging
import math
import time
import uuid
from collections.abc import Mapping
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.catalog import registry
from apps.catalog import selectors as catalog
from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.kernel import ai_log, flags, runtime
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.ai import oversight
from apps.kernel.errors import DomainError
from apps.listings import lifecycle, validation
from apps.listings import selectors as listings
from apps.listings.services import FieldsInvalid
from apps.valuation import features, forecasting, model_store, selectors
from apps.valuation.models import PriceEstimate, ValuationModelVersion
from apps.valuation.selectors import EstimateOutcome

logger = logging.getLogger("estate.valuation")

# What a caller may enter; `subtype` comes with the taxonomy. Nothing else (no price, no address).
ATTRIBUTE_FIELDS = tuple(name for name in features.SOURCE_FIELDS if name != "subtype")
TIMEOUT_SECONDS = 20  # the first call imports scikit-learn; later ones take milliseconds
ROUND_TO = 1000  # PKR; the model is no more precise than this
AI_FEATURE = "price_estimate"


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
    return _estimate(actor.account_id, wire, listing_id=None)


def estimate_for_listing(actor: Actor, listing_id) -> EstimateOutcome:
    """An estimate for the account's own Draft or Published Listing (the owner's recompute).

    Visitor `unauthenticated`; not the owner's (also unknown and malformed) `not_found`;
    Suspended or Deactivated `forbidden`; an Archived, Sold or Rented one `conflict`."""
    require_signed_in(actor)
    view = listings.get_owned_listing(actor, listing_id)
    _require_requester(actor)
    if view.status != "draft" and not lifecycle.is_published(view):
        raise DomainError("conflict")
    return _estimate(actor.account_id, view.fields, listing_id=view.id)


def recompute_public_listing(actor: Actor, listing_id) -> EstimateOutcome:
    """Compute and store the estimate of a publicly visible Listing from its public model inputs,
    on the owner's account. The platform's own work (events, lazy repair): no throttle.

    The system actor only; a Listing that is not publicly visible is `not_found`."""
    view = listings.public_listing(actor, listing_id)
    owner = listings.public_owner_id(actor, view.id)
    return _estimate(owner, selectors.listing_fields(view), listing_id=view.id)


def schedule_listing_estimate(actor: Actor, listing_id) -> None:
    """`recompute_public_listing` without making the caller wait: on the runtime executor's
    background worker, so it never takes a heavy slot a waiting user needs (in the calling
    thread when `VALUATION_BACKGROUND_ESTIMATES` is off, as in tests). When it cannot run
    or fails, the Listing has no current estimate until `current_estimate` or the owner repairs
    it."""
    if not settings.VALUATION_BACKGROUND_ESTIMATES:
        _scheduled(actor, listing_id, _infer_now)
        return
    try:
        # already on a worker: inference runs in this task, not in a second one
        runtime.submit_background(_scheduled, actor, listing_id, _infer_now)
    except runtime.ExecutorBusy:
        logger.warning("estimate_not_scheduled", extra={"event": "estimate_not_scheduled"})


def _scheduled(actor: Actor, listing_id, infer) -> None:
    try:
        view = listings.public_listing(actor, listing_id)
    except DomainError:  # gone or no longer public meanwhile
        return
    if selectors.current_model() is None:
        return
    owner = listings.public_owner_id(actor, view.id)
    _estimate(owner, selectors.listing_fields(view), listing_id=view.id, infer=infer)


def _estimate(
    account_id: uuid.UUID, fields: Mapping, listing_id: uuid.UUID | None, infer=None
) -> EstimateOutcome:
    locality = selectors.name_of(catalog.locality_names, fields.get("locality"))
    reason = selectors.unsupported_reason(fields, locality)
    if reason:
        return EstimateOutcome(reason=reason)
    if not flags.is_enabled(oversight.PRICE_ESTIMATE):  # switched off by an administrator
        return EstimateOutcome(reason="temporarily_unavailable")
    inputs = features.snapshot_of(fields, locality)
    model = selectors.current_model()
    value, reason = _run_model(model, inputs, infer or _infer_bounded)
    if reason:
        return EstimateOutcome(reason=reason)
    row = PriceEstimate.objects.create(
        account_id=account_id,
        listing_id=listing_id,
        model_version_id=model.id,
        feature_schema_version=features.FEATURE_SCHEMA_VERSION,
        inputs=inputs,
        inputs_sha256=features.snapshot_hash(inputs),
        value=value,
        forecast=forecasting.compute(value, locality),
        created_at=timezone.now(),
    )
    return EstimateOutcome(row.value, model.version, row.created_at, forecast=row.forecast)


def _infer(model: selectors.ModelVersionRef, inputs: dict) -> float | None:
    """On a worker thread; no database. None: the model does not cover these inputs."""
    loaded = selectors.load_model(model)
    try:
        return loaded.estimate(features.build_features(inputs))
    except features.UnsupportedInput:
        return None


def _infer_now(model: selectors.ModelVersionRef, inputs: dict) -> float | None:
    return _infer(model, inputs)


def _infer_bounded(model: selectors.ModelVersionRef, inputs: dict) -> float | None:
    """On a heavy slot with a time limit: for a caller that waits for the answer."""
    future = runtime.submit_heavy(_infer, model, inputs)
    try:
        return future.result(timeout=TIMEOUT_SECONDS)
    except FutureTimeout:
        future.cancel()
        raise


def _run_model(model, inputs: dict, infer) -> tuple[Decimal | None, str | None]:
    """(value, None) or (None, reason). Every attempt is in the AI request log, without content."""
    started = time.monotonic()
    value, reason, outcome = None, "temporarily_unavailable", "error"
    try:
        if model is not None:
            try:
                raw = infer(model, inputs)
            except FutureTimeout:
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
