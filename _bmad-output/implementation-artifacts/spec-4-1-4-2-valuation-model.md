# Stories 4.1 and 4.2: synthetic dataset, valuation model, registration, forecast parameters [BE]

Acceptance criteria: `planning-artifacts/epics.md`, Epic 4, Stories 4.1 and 4.2 (FR-77, AD-12, SM-4a).
Behaviour and assumptions: `docs/valuation.md`.

## Scope

- Seeded generator, training with a gate, committed artifact and manifest (`apps/valuation/data`).
- `ValuationModelVersion` (migration `valuation.0001`, schema only), `register_valuation_model`,
  `train_valuation_model`, hash-checked loading, one feature builder.
- Forecast scenario parameters `forecast_v1.yaml` with a loader.
- Registry version 11: `subtype` and `locality` are `valuation_input`.
- Dependencies: `scikit-learn`, `numpy`. No pandas, no experiment tracking, no pipeline.

Not here: estimate requests, supportability, stored results, executor use, forecast computation (4.3 to 4.7).

## Decisions

| Question | Decision |
| --- | --- |
| Artifact | Committed pickle plus JSON manifest; deploys register, never train |
| Registration | A command, not a data migration (a retrain would need a new migration each time) |
| Locality | Matched by name (ids differ per database) |
| `subtype`, `locality` | Flagged `valuation_input`, so AD-12 holds; a locality change now emits `listing.model_inputs_changed` |
| Baseline | By locality as specified, and also by locality and subtype (harder) |
| Building age | Counted from 2026, so the same inputs always give the same features |
| Library upgrade | Loading refuses another scikit-learn version: retrain as a new version |

## For Story 4.3

`selectors.current_model()`, `selectors.load_model(ref)`, `features.build_features(values)` with the locality name in
`values["locality"]` (raises `UnsupportedInput`), `LoadedModel.estimate(built)`. Run it on the bounded executor.
