---
title: 'Story 3.3: Filter schema and search selector (backend only)'
type: 'feature'
created: '2026-11-27'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Story 3.3)'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-1-browse-published-listings.md'
  - '{project-root}/docs/registry.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/visibility.md'
  - '{project-root}/docs/testing.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The public list cannot be narrowed.

**Approach:** `catalog.filters.ListingFilter` (an abstract specification generated from the registry's `searchable` flags: city, locality, category, subtype, purpose, price range, area range, bedrooms, bathrooms, parking, amenities, seller-declared nearby facilities, with required and preference criteria and sort options); `listings` translates it inside `search_public`, which always starts from `public_listings()`. The public list endpoint accepts the filters as query parameters. Keep it as simple as satisfies the acceptance criteria (final-year project; the AI search epic will reuse the same spec).

## Boundaries & Constraints

**Always:**
- Spec built and validated in `catalog` (catalog never imports Listing models); unknown parameter, bad value, inverted range → `400 validation_error` naming the parameter. A new searchable registry field appears in the filter without code changes elsewhere (test with a patched registry).
- Required criteria AND together; preference criteria only affect ranking (never exclude); results deterministic with id tiebreak, cursor-paginated for every sort (`newest`, `price_asc`, `price_desc`, and relevance-by-preference when preferences are given); the cursor is bound to the filter and sort (a mismatched cursor is `400`).
- A Listing with an unanswered attribute never matches a required criterion on it; area range uses normalised area (`area_sqft`), so a marla/kanal listing without a basis is excluded when filtering by area; amenities require state `present`; price filter semantics per purpose/price basis documented (decide: compare `asking_price` as stored; rent and sale are separated by `purpose`).
- `search_public` is the only function turning a spec into a query, starts from `public_listings()`; any counts do too. No N+1. Public caching stays as in 3.1 (response varies only by query string).
- Endpoint: `GET /api/v1/public/listings` gains the filter parameters (documented in openapi), plus `GET /api/v1/public/filters` returning the filter schema (fields, types, allowed values) generated from the registry, for the frontend panel.
- Tests for every criterion type, null exclusion, preference ranking, sorting and cursors, invalid input, visibility (hidden listings never match), query count; privacy gates still pass (no private field filterable).

**Never:** filter on a private-class field; query outside `public_listings()`; add Claude attribution to commits.

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

## Verification

ruff, format, lint-imports, makemigrations --check, spectacular, new tests, `-m guard`, one `pytest -n 3` full run.
