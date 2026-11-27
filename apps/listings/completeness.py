"""The completeness rules of a Listing: every reason it cannot be (or stay) published, all at once.

Shared by `listings.publication.publish` (a Draft becoming Published) and by
`listings.services.apply_updates` (an edit of a Published Listing, which must leave it complete):
one rule, no copy. Pure reads: nothing here writes.
"""

import uuid
from collections.abc import Mapping

from apps.catalog import registry
from apps.catalog import selectors as catalog
from apps.listings import validation
from apps.listings.fields import EDITABLE_FIELDS
from apps.listings.lifecycle import Origin
from apps.listings.models import PropertyListing
from apps.listings.selectors import ListingView

# The field ids that are fixed at creation: always present, never re-judged as input.
_CREATION_FIELDS = frozenset({"purpose", "category", "subtype"})
# The values a Seller must have confirmed before an AI-supplied one can be published (Story 5.12).
SENSITIVE_FIELDS = (
    "purpose",
    "category",
    "subtype",
    "asking_price",
    "locality",
    "area_value",
    "area_unit",
)


def _resolve(kind: str, key: uuid.UUID) -> uuid.UUID | None:
    """The catalog's answer for a stored city or locality: None when it is unknown or inactive."""
    if kind == "city":
        city = catalog.get_city(key)
        return city.id if city else None
    locality = catalog.get_locality(key)
    return locality.city_id if locality else None


def problems_of(
    row: PropertyListing,
    view: ListingView,
    has_images: bool,
    touched: frozenset[str] | None = None,
) -> dict[str, str]:
    """Every reason this Draft cannot be published, as a code per field (all at once).

    The required items come from the registry (`requirement: required_for_publication` plus
    applicability), so a registry change cannot drift from this rule. Every stored value that is
    present, required or optional, is judged again with the current per-field and cross-field
    rules (`validation.evaluate`), so stale invalid data blocks with a field-level error. Missing
    optional data never blocks."""
    errors: dict[str, str] = {}
    for definition in registry.fields():
        if definition.requirement != "required_for_publication" or definition.derived:
            continue
        if not definition.applies_to(row.purpose, row.category, row.subtype):
            continue
        value = view.fields.get(definition.id)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors[definition.id] = "required"
    present = {
        name: dict(view.fields[name])
        if isinstance(view.fields[name], Mapping)
        else view.fields[name]
        for name in EDITABLE_FIELDS
        if name not in _CREATION_FIELDS and view.fields.get(name) is not None
    }
    codes = catalog.amenity_codes() if "amenities" in present else None
    _, invalid = validation.evaluate(
        row.purpose,
        row.category,
        row.subtype,
        {"amenities": present.get("amenities")},  # a stored entry stays valid if its code retired
        present,
        _resolve,
        codes,
    )
    for name, code in invalid.items():
        if name != validation.NON_FIELD:  # a cross-field rule may name the partner field
            errors.setdefault(name, code)
    if not has_images:
        errors["images"] = "images_required"
    stored = row.provenance if isinstance(row.provenance, dict) else {}
    for name, entry in stored.items():
        definition = registry.field(name) if isinstance(name, str) else None
        if (
            definition is not None
            and (
                definition.quota_identity
                or definition.field_class == "private"
                or name in SENSITIVE_FIELDS
            )
            and definition.applies_to(row.purpose, row.category, row.subtype)
            and view.fields.get(name) is not None  # an empty field has nothing to confirm
            and isinstance(entry, dict)
            and entry.get("origin") == Origin.AI.value
            and entry.get("confirmed") is not True
        ):
            errors.setdefault(name, "confirm_required")
    if touched is not None:
        # A Published edit (Story 2.17) is only answerable for what it touches: a problem is kept
        # when the edit changed that field, when a required item is missing, or for the image
        # rule. Stored values this edit did not change (a city or locality deactivated since, a
        # rule tightened on another field) never block it.
        errors = {
            name: code
            for name, code in errors.items()
            if name in touched or code == "required" or name == "images"
        }
    return errors
