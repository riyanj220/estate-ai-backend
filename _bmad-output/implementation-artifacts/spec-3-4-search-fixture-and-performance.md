---
title: 'Story 3.4: Search fixture and performance budget'
type: 'feature'
created: '2026-11-27'
status: 'in-review'
---

## Intent

**Problem:** The NFR-1 budgets (search p95 under 500 ms, detail p95 under 300 ms server time) were not measured.

**Approach:** a repeatable generator of about 10,000 published Listings without images, a `perf` test outside the default
run, and indexes where `EXPLAIN ANALYZE` shows they matter. Backend only.

## Done

- `tests/perf/fixture.py`: the generator. `tests/perf/test_fixture.py` (default run) checks it; `test_performance.py`
  (`perf` marker) measures the budgets and checks that the newest list uses its index.
- `PropertyListing.first_published_at` (migration 0017 with backfill), set once by `publication`, with the partial index
  `listings_public_newest_idx`; the keyset condition bounds the first sort key.
- CI step for `-m perf`; `scripts/test-changed.sh` excludes `perf`.
- Three migration tests now do their model work before rolling the schema back (the new column exposed this).

## Decisions

- Stored time instead of the `(account_id, listing_id, created_at)` index noted in the 3.1 triage: the index alone left
  page 21 over budget.
- No management command: the publication guards forbid a generator under `apps/`, and the story makes the command
  optional.

## Measurements

p95, before to after: all searches 476 to 196 ms; newest page 21 571 to 86 ms; worst shape after (relevance) 224 ms;
detail 39 to 21 ms. Full table and conditions: `docs/performance.md`.
