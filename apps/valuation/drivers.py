"""What moves one estimate: for each detail the Listing states, the model is asked again with only
that detail swapped for a typical one, and the difference is the detail's effect.

Pure: it takes the model as a function. An effect is the model's own answer, not a market fact, and
the effects of several details do not add up to a total. Size is not a driver: it sets the scale of
the price, so it is held as it is."""

import math
from collections.abc import Callable

from apps.valuation import features

# The typical value of each detail (the middle of what the training data holds, `data.generator`).
TYPICAL_AGE = 20.0
TYPICAL_AMENITIES = 4.0
TYPICAL_ROAD_FT = 30.0
TYPICAL_COVERED_RATIO = {"house": 1.2, "apartment": 0.85}
TYPICAL_CONDITION = float(features.CONDITIONS.index("good"))
TYPICAL_DEVELOPMENT = float(features.DEVELOPMENT.index("developed"))
SMALLEST_PERCENT = 1.0  # below this the difference is model noise
LIMIT = 6


def _typical(built: dict) -> dict[str, tuple[str, float]]:
    """factor -> (feature, typical value), for the details this Listing states."""
    sqft = math.exp(built["log_area_sqft"])
    rooms = float(max(1, min(8, round(math.sqrt(sqft) / 12))))
    wanted = {
        "bedrooms": ("bedrooms", rooms),
        "bathrooms": ("bathrooms", built["bedrooms"]),
        "covered_area": (
            "covered_area_sqft",
            sqft * TYPICAL_COVERED_RATIO.get(built["subtype"], 1.0),
        ),
        "building_age": ("building_age", TYPICAL_AGE),
        "condition": ("condition", TYPICAL_CONDITION),
        "corner_plot": ("corner_plot", 0.0),
        "road_width": ("road_width_ft", TYPICAL_ROAD_FT),
        "development": ("development", TYPICAL_DEVELOPMENT),
        "amenities": ("amenity_count", TYPICAL_AMENITIES),
    }
    return {
        factor: (feature, typical)
        for factor, (feature, typical) in wanted.items()
        if not math.isnan(built[feature]) and not math.isnan(typical)
    }


def compute(
    built: dict, localities: list[str], estimate: Callable[[list[dict]], list[float]]
) -> list[dict]:
    """`[{"factor", "percent"}]`, the largest effect first; a positive percent raises the price.
    The locality is compared with the middle locality the model covers. `estimate` prices many
    feature sets at once."""
    typical = _typical(built)
    rows = [built, *({**built, "locality": name} for name in localities)]
    rows += [{**built, feature: value} for feature, value in typical.values()]
    prices = estimate(rows)
    own, elsewhere = prices[0], sorted(prices[1 : 1 + len(localities)])
    compared = {"locality": elsewhere[len(elsewhere) // 2]}
    compared.update(zip(typical, prices[1 + len(localities) :], strict=True))
    effects = [(factor, (own / other - 1) * 100) for factor, other in compared.items()]
    effects = [e for e in effects if math.isfinite(e[1]) and abs(e[1]) >= SMALLEST_PERCENT]
    effects.sort(key=lambda e: -abs(e[1]))
    return [{"factor": factor, "percent": f"{percent:.1f}"} for factor, percent in effects[:LIMIT]]
