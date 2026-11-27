"""Write the synthetic training dataset to a CSV file, for reading and for the report.

    python manage.py export_valuation_dataset [--output exports/valuation_dataset.csv]

Needs no database. The rows are regenerated from the fixed seed, so the file holds exactly what
the model was trained on. Every value is synthetic (docs/valuation.md).
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.valuation.data import generator, localities, training

COLUMNS = (
    "split",
    "subtype",
    "locality",
    "locality_tier",
    "area_value",
    "area_unit",
    "marla_basis",
    "bedrooms",
    "bathrooms",
    "construction_year",
    "condition",
    "covered_area_sqft",
    "corner_plot",
    "road_width_ft",
    "development_status",
    "amenity_count",
    "price",
)


def rows() -> list[dict]:
    """The dataset as flat rows; a blank cell is a value the row does not state."""
    data = generator.generate()
    tiers = localities.load().tiers
    _, held_out = training.split(len(data), generator.SEED)
    test_rows = set(held_out)
    flat = []
    for index, row in enumerate(data):
        values = {key: value for key, value in row.items() if key != "amenities"}
        amenities = row.get("amenities")
        flat.append(
            {
                **values,
                "split": "test" if index in test_rows else "train",
                "locality_tier": tiers[row["locality"]],
                "amenity_count": None if amenities is None else len(amenities),
                "price": int(row["price"]),
            }
        )
    return flat


class Command(BaseCommand):
    help = "Export the synthetic valuation dataset as CSV."
    requires_system_checks: list = []

    def add_arguments(self, parser):
        parser.add_argument("--output", type=Path, default=Path("exports/valuation_dataset.csv"))

    def handle(self, *args, **options):
        path: Path = options["output"]
        data = rows()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=COLUMNS)
                writer.writeheader()
                writer.writerows(data)
        except OSError:
            raise CommandError(f"Could not write {path}.") from None
        self.stdout.write(f"{len(data)} synthetic rows written to {path}.")
