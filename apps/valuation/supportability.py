"""What the valuation model can estimate (FR-46). Pure: no database, no actor.

`classify` gives None (supported) or a reason code; `MESSAGES` holds the short static text of each
code. An unsupported case never has a number."""

from apps.listings.area import MARLA_UNITS

SUPPORTED_PURPOSE = "sale"
SUPPORTED_PAIRS = frozenset(
    {("house", "house"), ("apartment", "apartment"), ("plot", "residential_plot")}
)
CITY = "Karachi"

# reason code -> the short static message shown instead of a number
MESSAGES = {
    "rent_not_supported": "Estimates are available for properties for sale only.",
    "type_not_supported": "Estimates are not available for this property type yet.",
    "locality_missing": "Choose a locality to get an estimate.",
    "locality_not_covered": "This locality is not covered yet.",
    "area_missing": "Enter the area and its unit to get an estimate.",
    "marla_basis_unknown": "Choose the marla size to get an estimate.",
    "temporarily_unavailable": "The estimate could not be calculated. Try again shortly.",
}


def classify(
    *,
    purpose: str,
    category: str,
    subtype: str,
    city: str | None,
    locality: str | None,
    area_value,
    area_unit: str | None,
    marla_basis,
    covered,
) -> str | None:
    """The first reason the inputs are unsupported, or None. `covered` holds the locality names
    of the model."""
    if purpose != SUPPORTED_PURPOSE:
        return "rent_not_supported"
    if (category, subtype) not in SUPPORTED_PAIRS:
        return "type_not_supported"
    if locality is None:
        return "locality_missing"
    if city != CITY or locality not in covered:
        return "locality_not_covered"
    if area_value is None or area_unit is None:
        return "area_missing"
    if area_unit in MARLA_UNITS and marla_basis is None:
        return "marla_basis_unknown"
    return None
