"""Asking price against the Estimated Property Value (Story 4.6, FR-44). Pure."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

ABOUT_EQUAL_WITHIN = Decimal(5)  # percent, inclusive
VERDICTS = ("about_equal", "above", "below")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Comparison:
    verdict: str  # the Asking price is `about_equal` to, `above` or `below` the estimate
    percent: Decimal  # how far from the estimate, one decimal, never negative


def total_asking_price(asking_price, price_basis, area_value) -> Decimal | None:
    """The total price: as asked, or a per-unit price times the area (in the Listing's own unit).
    None for a rental or incomplete figures."""
    if asking_price is None:
        return None
    if price_basis == "total":
        return Decimal(str(asking_price)).quantize(CENT, rounding=ROUND_HALF_UP)
    if price_basis == "per_unit" and area_value is not None:
        total = Decimal(str(asking_price)) * Decimal(str(area_value))
        return total.quantize(CENT, rounding=ROUND_HALF_UP)
    return None


def compare(asking_total: Decimal, estimate: Decimal) -> Comparison:
    difference = (asking_total - estimate) / estimate * 100
    percent = abs(difference).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if abs(difference) <= ABOUT_EQUAL_WITHIN:
        return Comparison("about_equal", percent)
    return Comparison("above" if difference > 0 else "below", percent)
