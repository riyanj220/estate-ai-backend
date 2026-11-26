"""Train and evaluate the valuation model (FR-77, SM-4a).

The model predicts log price per square foot; metrics are on the total price in PKR over a held-out
fifth of the synthetic dataset. Training writes nothing unless the model passes the gate."""

import hashlib
import json
import math
import pickle
import random
import statistics
from datetime import UTC, datetime
from pathlib import Path

from apps.valuation import features, model_store
from apps.valuation.data import generator, localities

TEST_SHARE = 0.2
MAX_MAPE = 0.20


class TrainingFailed(Exception):
    """The trained model does not meet the acceptance gate."""


def split(count: int, seed: int) -> tuple[list[int], list[int]]:
    order = list(range(count))
    random.Random(seed).shuffle(order)  # noqa: S311
    cut = count - round(count * TEST_SHARE)
    return order[:cut], order[cut:]


def _metrics(actual: list[float], predicted: list[float]) -> dict:
    errors = [abs(a - p) for a, p in zip(actual, predicted, strict=True)]
    mean = statistics.fmean(actual)
    residual = sum(e * e for e in errors)
    total = sum((a - mean) ** 2 for a in actual)
    return {
        "mae_pkr": round(statistics.fmean(errors)),
        "mape": round(statistics.fmean(e / a for e, a in zip(errors, actual, strict=True)), 4),
        "r2": round(1 - residual / total, 4),
    }


def _median_baseline(train: list, test: list, key) -> list[float]:
    """Median price per square foot of the group (training rows) times the area."""
    groups: dict = {}
    for row, area, price in train:
        groups.setdefault(key(row), []).append(price / area)
    overall = statistics.median(price / area for _, area, price in train)
    medians = {k: statistics.median(v) for k, v in groups.items()}
    return [medians.get(key(row), overall) * area for row, area, _ in test]


def fit(seed: int = generator.SEED, rows: int = generator.ROWS):
    """(estimator, locality list, report). Deterministic for one seed and library versions."""
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingRegressor
    from threadpoolctl import threadpool_limits

    data = generator.generate(seed, rows)
    names = sorted(localities.load().base_rates)
    built = [features.build_features(row) for row in data]
    areas = [math.exp(f["log_area_sqft"]) for f in built]
    prices = [row["price"] for row in data]
    x = np.array([features.encode(f, names) for f in built])
    y = np.log(np.array(prices) / np.array(areas))
    train, test = split(len(data), seed)
    estimator = HistGradientBoostingRegressor(
        learning_rate=0.08,
        max_iter=300,
        max_leaf_nodes=15,
        categorical_features=[features.FEATURE_NAMES.index(n) for n in features.CATEGORICAL],
        early_stopping=False,
        random_state=seed,
    )
    with threadpool_limits(limits=1):  # small data: one thread is fastest and repeatable
        estimator.fit(x[train], y[train])
        per_sqft = np.exp(estimator.predict(x[test]))
    actual = [prices[i] for i in test]
    sample = [(data[i], areas[i], prices[i]) for i in range(len(data))]
    train_rows, test_rows = [sample[i] for i in train], [sample[i] for i in test]
    report = {
        "train_rows": len(train),
        "test_rows": len(test),
        "model": _metrics(
            actual, [float(p) * areas[i] for p, i in zip(per_sqft, test, strict=True)]
        ),
        "baseline_locality": _metrics(
            actual, _median_baseline(train_rows, test_rows, lambda r: r["locality"])
        ),
        "baseline_locality_subtype": _metrics(
            actual,
            _median_baseline(train_rows, test_rows, lambda r: (r["locality"], r["subtype"])),
        ),
    }
    return estimator, names, report


def passes_gate(report: dict) -> bool:
    mape = report["model"]["mape"]
    return mape <= MAX_MAPE and all(
        mape < report[name]["mape"] for name in ("baseline_locality", "baseline_locality_subtype")
    )


def train_and_save(version: int, directory: Path = model_store.ARTIFACTS) -> dict:
    """Write the artifact and its manifest for `version`; return the manifest."""
    import sklearn

    estimator, names, report = fit()
    if not passes_gate(report):
        raise TrainingFailed(f"The model does not meet the gate: {json.dumps(report)}")
    blob = pickle.dumps(
        {"estimator": estimator, "localities": names, "features": features.FEATURE_NAMES},
        protocol=5,
    )
    manifest = {
        "name": model_store.MODEL_NAME,
        "version": version,
        "feature_schema_version": features.FEATURE_SCHEMA_VERSION,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sklearn_version": sklearn.__version__,
        "artifact_sha256": hashlib.sha256(blob).hexdigest(),
        "dataset": {
            "seed": generator.SEED,
            "rows": generator.ROWS,
            "sha256": generator.digest(generator.generate()),
            "localities_version": localities.load().version,
        },
        "metrics": report,
    }
    directory.mkdir(parents=True, exist_ok=True)
    model_store.artifact_path(version, directory).write_bytes(blob)
    model_store.manifest_path(version, directory).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
