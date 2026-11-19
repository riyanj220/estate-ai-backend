---
title: 'Story 1.2: Layered app skeleton and import contracts'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '762cbc65c6987ed6cf8abfaa389ff3d0140537bb'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-1-backend-repository-local-environment-and-database-roles.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The backend has no domain structure, so nothing yet stops later stories from coupling apps, importing another app's models or reaching upward.

**Approach:** Create the `apps/` package skeleton from AD-1, encode the dependency rules as import-linter contracts and a serializer lint, prove each rule fails on a deliberate violation, and run a spike recording how synchronous views behave under Daphne (AD-14).

## Boundaries & Constraints

**Always:** Python 3.12, import-linter as a dev dependency with its config in `pyproject.toml`; domain apps (`catalog`, `identity`, `listings`, `valuation`, `search`, `assistant`, `messaging`, `governance`) each have `services.py`, `selectors.py`, `events.py` and `subscribers.py`; `kernel` has an empty `__init__`; `presentation/{api,adminapi,realtime}` are plain packages; layer order bottom to top is kernel, catalog, identity, listings, tier-4 (valuation, search, assistant, messaging), governance, presentation; CI runs the contracts and the tests; code comments only where intent is not obvious.

**Never:** Add models, migrations, DRF, Channels, URLs or business logic; register the new apps in `INSTALLED_APPS` (they have no models yet; each story registers its own app); change Story 1.1 database or settings behaviour.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Skeleton | list `apps/` | all packages in AD-1 exist with the modules above | N/A |
| Clean tree | `lint-imports` on the repo | every contract passes | N/A |
| Upward import | fixture: a lower layer imports a higher one | layers contract fails | non-zero exit naming the import |
| Tier-4 peers | fixture: `valuation` imports `search` | independence contract fails | non-zero exit |
| Foreign models | fixture: one domain app imports another's `models` | forbidden contract fails | non-zero exit |
| Emitter imports subscriber | fixture: an app imports another app's `subscribers` | forbidden contract fails | non-zero exit |
| Assistant to listings models | fixture: `assistant` imports `listings.models` | forbidden contract fails | non-zero exit |
| Wildcard serializer | file with `fields = "__all__"` | lint test fails | message with file and line |
| Sync view under ASGI | one sync and one async view served by Daphne | observed concurrency recorded in `docs/architecture-notes.md` | if sync views run concurrently, flag AD-14 for amendment |

</frozen-after-approval>

## Code Map

Greenfield under `apps/`; `config/` and `tests/` exist from Story 1.1 (do not change their behaviour).

- `apps/**` -- package skeleton as above
- `pyproject.toml` -- add `import-linter` dev dependency; `[tool.importlinter]` contracts: `layers` (containers, exhaustive), tier-4 `independence`, `forbidden` per domain app for `models`, `forbidden` for `subscribers` (ignore `apps.*.apps`), `forbidden` for `assistant` to `listings.models`
- `tests/architecture/` -- `test_import_contracts.py` (clean tree passes; each violation fixture fails by copying `apps/` and the config to a temp dir and running `lint-imports` there), `test_serializer_lint.py` (AST scan of `apps/` for `fields = "__all__"`, with a failing fixture)
- `scripts/asgi_thread_spike.py` -- starts Daphne with one sync and one async view and measures overlap; result written to `docs/architecture-notes.md`
- `.github/workflows/ci.yml` -- add an import-contracts step
- `README.md` -- short architecture checks section

## Tasks & Acceptance

**Execution:**
- [x] `apps/**` -- create the skeleton -- AD-1 layout
- [x] `pyproject.toml`, `uv.lock` -- add import-linter and contracts -- AD-2, AD-3
- [x] `tests/architecture/` -- contract and serializer tests with failing fixtures -- proves the rules bite
- [x] `scripts/asgi_thread_spike.py`, `docs/architecture-notes.md` -- run the spike and record the result -- AD-14
- [x] `.github/workflows/ci.yml`, `README.md` -- run contracts in CI; document

**Acceptance Criteria:**
- Given the repository, when I list `apps/`, then every AD-1 package exists, each domain app with `services`, `selectors` and `events`.
- Given the import-linter config, when it runs, then all contracts pass, and each deliberate violation in a test fixture makes its contract fail.
- Given the serializer lint, when a file sets `fields = "__all__"`, then the lint test fails.
- Given the spike, when it has run, then `docs/architecture-notes.md` records the observed behaviour and, if the assumption is false, flags AD-14 for amendment.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | Seven of eight models contracts lack a failing fixture (VG) | medium | patch | Removing a contract left all tests green; one parametrised fixture per app added. |
| 2 | Subscribers ignore wildcard allows cross-app `apps.py` imports (BH, EC, VG) | medium | patch | Wildcard matched `identity.apps -> catalog.subscribers`; replaced by per-app ignores plus fixture. |
| 3 | Contract lists hand-maintained, can drift from the filesystem (BH, EC) | medium | patch | Completeness test derives apps from the filesystem. |
| 4 | Skeleton (AC 1) and exhaustive layers flag untested (VG) | medium | patch | Skeleton test and unlisted-app fixture added; adjacent-layer ordering fixtures not added (low). |
| 5 | Test helpers fragile: executable path, no timeout, PYTHONPATH, trailing newline (EC, BH) | low | patch | Direct corrections. |
| 6 | Serializer lint misses annotated, attribute, list/tuple forms; syntax errors crash (EC, BH, VG) | medium | patch | Widened and fixtured. |
| 7 | Spike script silent on startup failure, TimeoutExpired, always exit 0 (EC, BH) | low | patch | Fails loudly with captured stderr. |
| 8 | Spike conclusion overstated; thread-pool claims unmeasured (BH, EC) | medium | patch | Notes now state conditions; pool claims marked not measured. |
| 9 | AD-14 result not propagated to context and spine (BH) | medium | patch | Epic context and spine assumption updated. |
| 10 | Empty `models.py` undocumented (BH, EC, VG) | low | patch | Design Notes line added. |
| 11 | README lacks subscribers convention (BH) | low | patch | One line added. |
| 12 | `uv.lock` missing from diff (BH) | false | reject | Diff excluded `uv.lock`; lock contains import-linter (VG confirmed). |
| 13 | A12 line in epic context is scope creep (BH) | false | reject | Intentional context refresh required by the cache validity rule. |
| 14 | Spec status and sprint-status inconsistent (BH) | false | reject | Transient mid-workflow state; finalised in step 5. |
| 15 | Models package submodules not covered (EC) | false | reject | import-linter forbidden modules cover descendants. |
| 16 | Spike TOCTOU port, tri-state classification, pool saturation burst (EC) | low | reject | Spike is a one-off; fix adds complexity. |
| 17 | Layers ordering and sibling-violation fixtures (VG) | low | reject | Contract file is the ordering source; unlikely everyday defect. |
| 18 | Spike has no regression check in CI (VG) | low | reject | One-off human evidence by design. |

## Design Notes

Decisions made without asking: import-linter (the spine names it); fixtures copy the real tree to a temp directory so the real contract file is what is proven; a per-app `subscribers` module is the convention for event handlers registered in `AppConfig.ready`, which is what makes the "emitter imports subscriber" contract expressible; new apps stay out of `INSTALLED_APPS` until they own models. Each domain app has an empty `models.py` so the per-app forbidden-models contracts can resolve their target module.

## Verification

**Commands:**
- `uv sync --frozen && uv run lint-imports` -- expected: all contracts kept
- `uv run ruff check . && uv run pytest` -- expected: all pass
