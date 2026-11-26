"""Story 4.1: a reproducible dataset and a model that passes the SM-4a gate."""

import pytest

from apps.catalog import selectors as catalog
from apps.valuation import features, model_store
from apps.valuation.data import generator, localities, training


def test_the_same_seed_gives_the_identical_dataset():
    first, second = generator.generate(), generator.generate()
    assert first == second and generator.digest(first) == generator.digest(second)
    assert generator.digest(generator.generate(seed=1)) != generator.digest(first)


@pytest.mark.django_db
def test_the_dataset_covers_every_seeded_karachi_locality_and_supported_subtype():
    city = next(c for c in catalog.list_cities() if c.name == "Karachi")
    seeded = {locality.name for locality in catalog.list_localities(city.id)}
    assert set(localities.load().base_rates) == seeded
    data = generator.generate()
    assert {(row["locality"], row["subtype"]) for row in data} == {
        (name, subtype) for name in seeded for subtype in features.SUBTYPES
    }
    assert all(row["price"] > 0 for row in data)


def test_training_meets_the_gate_and_reproduces_the_committed_metrics():
    _, names, report = training.fit()
    assert training.passes_gate(report)
    assert report["model"]["mape"] <= 0.20
    assert report["model"]["mape"] < report["baseline_locality"]["mape"]
    assert report["model"]["mape"] < report["baseline_locality_subtype"]["mape"]
    assert report["test_rows"] == generator.ROWS * training.TEST_SHARE
    committed = model_store.read_manifest(model_store.versions()[-1])
    assert committed["dataset"]["sha256"] == generator.digest(generator.generate())
    assert report["model"]["mape"] == pytest.approx(
        committed["metrics"]["model"]["mape"], abs=0.005
    )
    assert names == sorted(localities.load().base_rates)


def test_a_model_that_misses_the_gate_is_not_written(monkeypatch, tmp_path):
    bad = {"mape": 0.30}
    report = {"model": bad, "baseline_locality": bad, "baseline_locality_subtype": bad}
    monkeypatch.setattr(training, "fit", lambda: (None, [], report))
    with pytest.raises(training.TrainingFailed):
        training.train_and_save(9, tmp_path)
    assert list(tmp_path.iterdir()) == []
