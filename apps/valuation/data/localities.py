"""The locality table (`localities.yaml`): tier and synthetic base rate per Karachi locality.

Used by the dataset generator and, for the tier of a locality, by the forecast."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parent / "localities.yaml"
TIERS = ("premium", "upper_mid", "mid", "affordable")


@dataclass(frozen=True)
class LocalityTable:
    version: int
    base_rates: dict[str, int]  # name -> PKR per square foot
    tiers: dict[str, str]  # name -> tier


@lru_cache(maxsize=1)
def load() -> LocalityTable:
    raw = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    rows = raw["localities"]
    return LocalityTable(
        version=raw["version"],
        base_rates={name: row["base_rate"] for name, row in rows.items()},
        tiers={name: row["tier"] for name, row in rows.items()},
    )
