"""The synthetic Karachi sale dataset (FR-77): reproducible from a seed, standard library only.

Every number here is an assumption invented for the project, documented in `docs/valuation.md`;
none of it is market data. Rows are shaped like Listing fields (registry ids), so training builds
features with the same code as inference."""

import hashlib
import json
import math
import random

from apps.valuation.data import localities

SEED = 20261127
ROWS = 15_000
NOISE_SIGMA = 0.10  # log-normal noise on the price
MISSING = 0.25  # chance that a recommended field is left empty (it still moved the price)

SUBTYPE_MULTIPLIER = {"house": 1.0, "apartment": 0.75, "residential_plot": 0.70}
CONDITION_MULTIPLIER = {"new": 1.08, "excellent": 1.04, "good": 1.0, "needs_renovation": 0.85}
DEVELOPMENT_MULTIPLIER = {"developed": 1.0, "partially_developed": 0.85, "undeveloped": 0.70}
SIZE_ELASTICITY = -0.08  # larger properties cost a little less per square foot
TYPICAL_SQFT = {"house": 2160, "apartment": 1300, "residential_plot": 2160}
HOUSE_SQYD = (80, 120, 200, 240, 300, 400, 500, 600, 1000, 2000)
PLOT_SQYD = (80, 120, 200, 240, 400, 500, 600, 1000, 2000)
AMENITY_CODES = tuple(f"synthetic_{n}" for n in range(8))  # only the count is a feature


def _area(rng: random.Random, subtype: str) -> tuple[dict, float]:
    """The area as a Seller would state it (value, unit, marla basis) and in square feet."""
    if subtype == "apartment":
        sqft = rng.randrange(450, 3600, 50)
        return {"area_value": sqft, "area_unit": "sqft"}, float(sqft)
    if rng.random() < 0.15:
        marla = rng.choice((3, 5, 8, 10, 20))
        basis = rng.choice(("225", "250", "272.25"))
        return (
            {"area_value": marla, "area_unit": "marla", "marla_basis": basis},
            marla * float(basis),
        )
    sqyd = rng.choice(HOUSE_SQYD if subtype == "house" else PLOT_SQYD)
    return {"area_value": sqyd, "area_unit": "sqyd"}, sqyd * 9.0


def _building(rng: random.Random, subtype: str, sqft: float) -> tuple[dict, float]:
    """House and apartment attributes and their price multiplier."""
    typical_rooms = max(1, min(8, round(math.sqrt(sqft) / 12)))
    bedrooms = max(0, typical_rooms + rng.choice((-1, 0, 0, 1)))
    bathrooms = max(1, bedrooms + rng.choice((-1, 0, 0, 1)))
    age = rng.randrange(0, 41)
    condition = rng.choices(tuple(CONDITION_MULTIPLIER), weights=(2, 3, 4, 1))[0]
    typical_ratio = 1.2 if subtype == "house" else 0.85
    ratio = typical_ratio * rng.uniform(0.7, 1.3)
    multiplier = (
        (1 + 0.02 * (bedrooms - typical_rooms))
        * (1 + 0.01 * (bathrooms - bedrooms))
        * (1 - 0.006 * age)
        * CONDITION_MULTIPLIER[condition]
        * (ratio / typical_ratio) ** 0.2
    )
    values = {
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "construction_year": 2026 - age,
        "condition": condition,
        "covered_area_sqft": round(sqft * ratio),
    }
    return values, multiplier


def _plot(rng: random.Random) -> tuple[dict, float]:
    corner = rng.random() < 0.15
    road = rng.choice((20, 24, 30, 30, 40, 60, 80, 100))
    development = rng.choices(tuple(DEVELOPMENT_MULTIPLIER), weights=(6, 3, 1))[0]
    multiplier = (
        (1.10 if corner else 1.0) * (road / 30) ** 0.08 * DEVELOPMENT_MULTIPLIER[development]
    )
    values = {"corner_plot": corner, "road_width_ft": road, "development_status": development}
    return values, multiplier


def generate(seed: int = SEED, rows: int = ROWS) -> list[dict]:
    """`rows` listings, each registry-shaped (`locality` is the name) plus `price` in PKR."""
    rng = random.Random(seed)  # noqa: S311 - synthetic data, not cryptography
    table = localities.load()
    names = sorted(table.base_rates)
    data = []
    for index in range(rows):
        subtype = tuple(SUBTYPE_MULTIPLIER)[index % 3]
        locality = rng.choice(names)
        values, sqft = _area(rng, subtype)
        attributes, multiplier = (
            _plot(rng) if subtype == "residential_plot" else _building(rng, subtype, sqft)
        )
        present = rng.randrange(0, len(AMENITY_CODES) + 1)
        attributes["amenities"] = {code: "present" for code in AMENITY_CODES[:present]}
        for name in list(attributes):
            if name not in ("bedrooms", "bathrooms") and rng.random() < MISSING:
                del attributes[name]
        price = (
            table.base_rates[locality]
            * sqft
            * SUBTYPE_MULTIPLIER[subtype]
            * (sqft / TYPICAL_SQFT[subtype]) ** SIZE_ELASTICITY
            * multiplier
            * (1 + 0.008 * present)
            * math.exp(rng.gauss(0, NOISE_SIGMA))
        )
        data.append(
            {
                "subtype": subtype,
                "locality": locality,
                **values,
                **attributes,
                "price": round(price, -3),
            }
        )
    return data


def digest(data: list[dict]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
