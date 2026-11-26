"""The versioned forecast scenario parameters (`forecast_v<n>.yaml`). The forecast itself is
Story 4.7; this only loads and validates the parameters."""

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml

from apps.valuation.data import localities

FORECAST_VERSION = 1


@dataclass(frozen=True)
class Growth:
    low: Decimal
    base: Decimal
    high: Decimal


@dataclass(frozen=True)
class ForecastParameters:
    version: int
    localities_version: int
    horizon_years: int
    annual_growth: dict[str, Growth]  # tier -> yearly rates, 0.08 is 8%


def path_of(version: int) -> Path:
    return Path(__file__).resolve().parent / f"forecast_v{version}.yaml"


@lru_cache
def load(version: int = FORECAST_VERSION) -> ForecastParameters:
    raw = yaml.safe_load(path_of(version).read_text(encoding="utf-8"))
    growth = {
        tier: Growth(**{k: Decimal(v) for k, v in rates.items()})
        for tier, rates in raw["annual_growth"].items()
    }
    if raw["version"] != version or set(growth) != set(localities.TIERS):
        raise ValueError(f"forecast_v{version}.yaml: wrong version or tiers.")
    if not all(Decimal(0) <= g.low <= g.base <= g.high for g in growth.values()):
        raise ValueError(f"forecast_v{version}.yaml: need 0 <= low <= base <= high.")
    return ForecastParameters(
        version=version,
        localities_version=raw["localities_version"],
        horizon_years=raw["horizon_years"],
        annual_growth=growth,
    )
