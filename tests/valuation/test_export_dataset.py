import csv

from django.core.management import call_command

from apps.valuation.data import generator
from apps.valuation.management.commands import export_valuation_dataset as export


def test_the_export_holds_the_training_rows(tmp_path):
    target = tmp_path / "nested" / "dataset.csv"
    call_command("export_valuation_dataset", output=target)
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    data = generator.generate()
    assert len(rows) == generator.ROWS
    assert tuple(rows[0]) == export.COLUMNS
    assert [int(r["price"]) for r in rows[:50]] == [int(d["price"]) for d in data[:50]]
    assert [r["locality"] for r in rows[:50]] == [d["locality"] for d in data[:50]]
    assert sum(r["split"] == "test" for r in rows) == generator.ROWS // 5
    assert {r["locality_tier"] for r in rows} == {"premium", "upper_mid", "mid", "affordable"}


def test_every_generated_key_has_a_column():
    keys = {key for row in generator.generate() for key in row} - {"amenities"}
    assert keys <= set(export.COLUMNS)
