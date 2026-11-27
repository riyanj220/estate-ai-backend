"""The Draft's public values as plain text: the conversation summary (Story 5.11) and the facts a
description may use (Story 5.13). Private, derived and empty fields are never included."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from apps.catalog import registry
from apps.catalog import selectors as catalog
from apps.listings.selectors import ListingView

_UNITS = {"sqft": "square feet", "sqyd": "square yards"}
# Words that name a nearby facility, so a mention can be checked against what was said.
SYNONYMS = {
    "nearby_mosque": ("mosque", "masjid", "masjed"),
    "nearby_park": ("park", "bagh"),
    "nearby_gym": ("gym", "fitness"),
    "nearby_school": ("school", "madrasa"),
    "nearby_hospital": ("hospital", "haspatal"),
}


@dataclass(frozen=True)
class Fact:
    field: str
    value: str = field(repr=False)
    confirmed: bool  # False for an AI value the Seller has not confirmed


def mentions(words: tuple[str, ...], text: str) -> bool:
    """Whole words only (plurals allowed): "parking" is not a park."""
    pattern = "|".join(map(re.escape, words))
    return re.search(rf"\b(?:{pattern})(?:e?s)?\b", text, re.IGNORECASE) is not None


def amenity_labels() -> dict[str, str]:
    return {a.code: a.label for a in catalog.list_amenities()}


def amenities_text(states: Mapping[str, str]) -> str:
    """ "Mosque, Park, no Gym", in catalogue order."""
    labels = amenity_labels()
    return ", ".join(
        label if states[code] == "present" else f"no {label}"
        for code, label in labels.items()
        if code in states
    )


def _text(spec: registry.FieldDef, value) -> str:
    if spec.type == "amenities":
        return amenities_text(value)
    if spec.type == "reference":
        found = (catalog.get_city if spec.reference == "city" else catalog.get_locality)(value)
        return found.name if found else ""
    if spec.type == "boolean":
        return "yes" if value else "no"
    if spec.type == "money":
        return f"{str(value).removesuffix('.00')} PKR"
    if spec.type == "enum":
        return _UNITS.get(value, str(value).replace("_", " "))
    if spec.type == "decimal":
        text = str(value)
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value)


def facts_of(listing: ListingView) -> tuple[Fact, ...]:
    """In registry order. The description itself is not a fact about the property."""
    found = []
    for spec in registry.fields():
        value = listing.fields.get(spec.id)
        if spec.derived or spec.field_class != "public" or spec.id == "description":
            continue
        if value is None or value == "":
            continue
        text = _text(spec, value)
        if text:
            confirmed = listing.provenance.get(spec.id, {}).get("confirmed", True)
            found.append(Fact(spec.id, text, bool(confirmed)))
    return tuple(found)
