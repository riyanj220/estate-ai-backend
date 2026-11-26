---
title: 'Story 3.1: Browse Published listings and view details (backend half, then frontend half)'
type: 'feature'
created: '2026-11-26'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Epic 3, Story 3.1)'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-12-visibility-policy-and-public-projections.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-13-draft-preview-and-management.md'
  - '{project-root}/docs/visibility.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/testing.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nobody can see the marketplace: Published Listings have no public route or page.

**Approach:** Backend: `listings.selectors.search_public(actor, spec, sort, cursor, limit)` (skeleton: empty spec, always starting from `public_listings()`), a public list endpoint and a public detail endpoint built only from the public projection. Frontend: server-rendered browse and detail pages. Filters are Story 3.3 onwards. Final-year project: keep it plain.

## Boundaries & Constraints

**Backend:**
- `search_public`: actor first; `spec` is an empty placeholder object now (3.3 fills it); `sort` supports `newest` (default; publication/creation order, deterministic with an id tiebreak) ; opaque cursor pagination following the existing pattern; `limit` bounded (default 20, max 50). Only `public_listings()` as the base; no N+1 (images, amenities, location batched; test the query count is constant for 1 vs 20 listings).
- `GET /api/v1/listings/public` (name it to avoid clashing with owner routes, for example `/api/v1/public/listings`) → `{results:[card], next}` where card is a compact public shape (id, purpose, category, subtype, title, price + basis/period, area, city and locality names or ids, bedrooms/bathrooms, cover image url, published date); `GET /api/v1/public/listings/{id}` → the full public projection (images, description, attributes, amenities, seller-declared nearby, public location) plus published date; never any private-class field, phone, provenance, revision, owner account id or status internals. Not publicly visible or unknown: the same `404`. AllowPublic, no CSRF needed, safe caching: `Cache-Control: public, max-age=60` only if the response does not vary by viewer (it must not); otherwise no-store. No cookie set.
- Serializers explicit; shared with the preview where possible (do not duplicate the field list). Regenerate openapi and the authz matrix; docs (visibility, listings).
- Tests: only publicly visible Listings (draft/archived/sold/rented/removed/restricted/suspended or unverified owner never appear in list or detail), no private values (canaries), pagination (no duplicates, stable), query count, headers, 404 shape.

**Frontend (after merge and api:sync):** `/listings` browse page and `/listings/[id]` detail page, server components that fetch from Django forwarding the cookie, accessible, keyboard usable, mobile friendly: cards with cover image and key facts, "load more" or next-page link, detail with image gallery, attributes via the registry labels, amenities grouped with the "seller-declared" label for nearby, public location, empty/error/not-found states, header nav link "Browse". Stub, spec with axe, README.

**Never:** expose private values; query anything but `public_listings()`; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior |
|----------|--------------|---------------------------|
| Browse | visitor | paginated cards of public listings only |
| Detail | public id | full public projection |
| Hidden / unknown id | draft, archived, suspended owner... | `404` |
| Bad cursor or limit | any | `400` |
| Empty marketplace | none | empty list |

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

## Verification

Backend: ruff, format, lint-imports, makemigrations --check, spectacular, narrow tests and one `pytest -n 3` full run. Frontend: lint, typecheck, build, api:check, new specs, `npm run test:e2e:changed`; full suite in CI.
