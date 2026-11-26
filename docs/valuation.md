# Valuation: synthetic data, model and forecast parameters (FR-77, AD-12)

**All data here is synthetic.** The base rates, multipliers, noise and growth rates were invented for this project. The
reported accuracy shows that the pipeline works on that synthetic data; it says nothing about the real Karachi market
(SM-4a). Estimates approximate asking prices, not transaction prices.

## What is covered

- Sale only; (Category, Subtype) pairs (House, House), (Apartment, Apartment), (Plot, Residential Plot). Commercial
  Plot, Plot File, Shop and Office are not covered. Purpose and supportability are judged by the caller (Story 4.3).
- Karachi only: all 71 localities seeded by `catalog` migration `0004`, matched by name. A locality added or renamed
  later is not covered until the model is retrained.
- Marla and kanal areas need a marla basis.

## Where things live (`apps/valuation`)

| File | Content |
| --- | --- |
| `data/localities.yaml` | tier and base rate of each locality (versioned) |
| `data/generator.py` | the synthetic dataset |
| `data/training.py` | split, model, metrics, gate |
| `data/forecast_v1.yaml`, `data/forecast.py` | forecast scenario parameters and their loader |
| `data/artifacts/` | committed model (`.pkl`) and its manifest (`.json`) |
| `features.py` | the one feature builder (training and inference) |
| `model_store.py` | artifact paths and hash-checked loading |

## Dataset assumptions

15 000 rows, seed `20261127`, Python's `random` only (no download, nothing stored: it is regenerated). Subtypes rotate
evenly; the locality is uniform. The manifest records the SHA-256 of the dataset the model was trained on.

`price = base_rate(locality) x area_sqft x subtype x size x attributes x amenities x noise`, rounded to PKR 1 000.

| Factor | Assumption |
| --- | --- |
| Base rate (PKR per sq ft) | per locality in `localities.yaml`: premium 24 000 to 38 000, upper_mid 14 500 to 21 000, mid 8 500 to 14 500, affordable 3 500 to 8 000 |
| Subtype | house 1.00, apartment 0.75, residential plot 0.70 |
| Size | `(area / typical) ^ -0.08`; typical 2 160 sq ft (house, plot), 1 300 (apartment) |
| Areas | apartment 450 to 3 550 sq ft; house 80 to 2 000 sq yd; plot 80 to 2 000 sq yd; 15% of houses and plots stated in marla (basis 225, 250 or 272.25) |
| Bedrooms | typical count from the area; +/-2% per bedroom above or below it |
| Bathrooms | +/-1% per bathroom above or below the bedroom count |
| Age | 0 to 40 years, -0.6% per year |
| Condition | new 1.08, excellent 1.04, good 1.00, needs renovation 0.85 |
| Covered area | around 1.2 x area (house), 0.85 x (apartment); `(ratio / typical) ^ 0.2` |
| Corner plot | 1.10 (15% of plots) |
| Road width | `(feet / 30) ^ 0.08` |
| Development | developed 1.00, partially 0.85, undeveloped 0.70 |
| Amenities | +0.8% per present amenity (0 to 8) |
| Noise | log-normal, sigma 0.10 |
| Missing values | each recommended field is blank in 25% of rows, although it still moved the price |

The generator and split were not tuned to improve the metrics (SM-C2).

## Features (feature schema version 1)

Only registry fields flagged `valuation_input` are read (`features.SOURCE_FIELDS`, guarded by
`tests/valuation/test_features.py`); the private address, phone and Asking price are never read. Registry version 11
flagged `subtype` and `locality` for this.

| Feature | From |
| --- | --- |
| subtype, locality (categorical) | `subtype`, `locality` (name) |
| log_area_sqft | `area_value`, `area_unit`, `marla_basis` |
| bedrooms, bathrooms, covered_area_sqft | same ids |
| building_age (from 2026) | `construction_year` |
| condition, development (ordinal) | `condition`, `development_status` |
| corner_plot, road_width_ft | same ids |
| amenity_count | `amenities` (number marked present) |

Missing optional values stay missing (the model handles them). `plot_width`, `plot_depth` and `frontage_ft` are flagged
but unused (the area covers the first two; frontage applies to shops only).

## Model and evaluation

scikit-learn `HistGradientBoostingRegressor` (300 trees, 15 leaves, learning rate 0.08) predicting log price per square
foot. 80/20 split by the same seed; metrics are on the total price of the 3 000 held-out rows. Baselines are the median
training price per square foot times the area, grouped by locality (SM-4a) and, as a harder check, by locality and
subtype. Gate: MAPE at most 20% and below both baselines, else nothing is written.

Version 1 (trained with scikit-learn 1.9.1; artifact 615 KB, in git), on the synthetic held-out rows:

| Predictor | MAE (PKR) | MAPE | R² |
| --- | --- | --- | --- |
| Model | 3 985 267 | 9.74% | 0.978 |
| Baseline: locality | 6 979 649 | 17.17% | 0.934 |
| Baseline: locality and subtype | 5 883 160 | 13.87% | 0.948 |

Log-normal noise of sigma 0.10 alone puts the best possible MAPE near 8%.

## Registering, loading, retraining

```bash
uv run python manage.py register_valuation_model          # after migrate; idempotent
uv run python manage.py train_valuation_model --model-version 2   # writes data/artifacts/
```

- The artifact is committed, so a deploy needs no training: migrate, then register.
- `ValuationModelVersion` records name, version, feature-schema version, metrics, training time and the artifact's
  SHA-256. `selectors.current_model()` is the highest registered version for this code's feature schema.
- A pickle can run code, so `selectors.load_model` unpickles only bytes whose SHA-256 equals the registered one. The
  hash protects against a changed or damaged file; whoever can commit to the repository or write the database is trusted.
- The scikit-learn version must equal the training one (`uv.lock` pins it). Upgrading it means retraining as a new
  version, in the same change.
- To retrain: change the data or code, train the next version, commit both files, register after deploying. Raise
  `FEATURE_SCHEMA_VERSION` if features change. A registered version never changes its artifact.

## Forecast scenario parameters (version 1)

Assumed yearly growth by locality tier, compounded over 5 years: `value(n) = estimate x (1 + rate) ^ n`. These are
scenarios, not predictions or guaranteed returns. The computation arrives with Story 4.7.

| Tier | Low | Base | High |
| --- | --- | --- | --- |
| premium | 5% | 9% | 13% |
| upper_mid | 5% | 8% | 12% |
| mid | 4% | 8% | 11% |
| affordable | 3% | 7% | 10% |

A released file is never edited (a test pins its hash): add `forecast_v2.yaml` and raise `FORECAST_VERSION`.
