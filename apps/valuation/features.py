"""The model's input features (AD-12), built from registry fields flagged `valuation_input` only.

One builder serves training and inference. It reads exactly the ids in `SOURCE_FIELDS`, so nothing
else in the mapping (private address, phone, Asking price) can reach the model. Any change to the
features or their encoding raises `FEATURE_SCHEMA_VERSION` and needs a retrained model."""

import hashlib
import json
import math
from collections.abc import Mapping
from decimal import Decimal

from apps.listings.area import normalised_sqft

FEATURE_SCHEMA_VERSION = 1
SUBTYPES = ("house", "apartment", "residential_plot")  # the supported pairs, for sale
REFERENCE_YEAR = 2026  # building age counts from here, so a feature vector never depends on today
CONDITIONS = ("needs_renovation", "good", "excellent", "new")  # worst to best
DEVELOPMENT = ("undeveloped", "partially_developed", "developed")

# feature -> the registry fields it is computed from
FEATURE_SOURCES = {
    "subtype": ("subtype",),
    "locality": ("locality",),
    "log_area_sqft": ("area_value", "area_unit", "marla_basis"),
    "bedrooms": ("bedrooms",),
    "bathrooms": ("bathrooms",),
    "covered_area_sqft": ("covered_area_sqft",),
    "building_age": ("construction_year",),
    "condition": ("condition",),
    "corner_plot": ("corner_plot",),
    "road_width_ft": ("road_width_ft",),
    "development": ("development_status",),
    "amenity_count": ("amenities",),
}
FEATURE_NAMES = tuple(FEATURE_SOURCES)
CATEGORICAL = ("subtype", "locality")
SOURCE_FIELDS = tuple(dict.fromkeys(f for fields in FEATURE_SOURCES.values() for f in fields))


class UnsupportedInput(ValueError):
    """The model cannot estimate these inputs; the message is a short reason code."""


def _number(value) -> float:
    return math.nan if value is None else float(value)


def _rank(value, order: tuple[str, ...]) -> float:
    return float(order.index(value)) if value in order else math.nan


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def build_features(values: Mapping) -> dict:
    """`values` maps registry field ids to values; `locality` is the locality NAME. Missing
    optional values become NaN (the model handles them). Raises `UnsupportedInput`."""
    read = {name: values.get(name) for name in SOURCE_FIELDS}
    if read["subtype"] not in SUBTYPES:
        raise UnsupportedInput("subtype")
    if not read["locality"]:
        raise UnsupportedInput("locality")
    area = normalised_sqft(
        _decimal(read["area_value"]), read["area_unit"], _decimal(read["marla_basis"])
    )
    if area is None or area <= 0:
        raise UnsupportedInput("area")
    year = read["construction_year"]
    corner = read["corner_plot"]
    amenities = read["amenities"]
    return {
        "subtype": read["subtype"],
        "locality": read["locality"],
        "log_area_sqft": math.log(float(area)),
        "bedrooms": _number(read["bedrooms"]),
        "bathrooms": _number(read["bathrooms"]),
        "covered_area_sqft": _number(read["covered_area_sqft"]),
        "building_age": math.nan if year is None else float(max(0, REFERENCE_YEAR - int(year))),
        "condition": _rank(read["condition"], CONDITIONS),
        "corner_plot": _number(corner),
        "road_width_ft": _number(read["road_width_ft"]),
        "development": _rank(read["development_status"], DEVELOPMENT),
        "amenity_count": math.nan
        if amenities is None
        else float(sum(state == "present" for state in amenities.values())),
    }


def snapshot_of(fields: Mapping, locality: str) -> dict:
    """The non-private model inputs only (`SOURCE_FIELDS`), the locality by name."""
    inputs = {name: fields.get(name) for name in SOURCE_FIELDS}
    inputs["locality"] = locality
    if inputs["amenities"] is not None:
        inputs["amenities"] = dict(sorted(inputs["amenities"].items()))
    return inputs


def snapshot_hash(inputs: Mapping) -> str:
    text = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def encode(features: dict, localities: list[str]) -> list[float]:
    """The numeric row in `FEATURE_NAMES` order. `localities` is the model's own list."""
    if features["locality"] not in localities:
        raise UnsupportedInput("locality")
    codes = {
        "subtype": float(SUBTYPES.index(features["subtype"])),
        "locality": float(localities.index(features["locality"])),
    }
    return [codes.get(name, features[name]) for name in FEATURE_NAMES]
