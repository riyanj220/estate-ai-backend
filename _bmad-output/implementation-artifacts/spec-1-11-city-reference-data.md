---
title: 'Story 1.11: City reference data'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '6dc82b9aac22939d4db21c1370b2edd4761465a5'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-10-audit-trail-and-reasoned-private-reads.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Agencies and Listings need to choose a city consistently, but there is no controlled list, and the `catalog` app is still an empty skeleton.

**Approach:** Make `catalog` the first installed domain app. It owns a `City` table seeded with Karachi by a data migration, writes it only through its services, exposes it to other apps only through selectors, and serves it publicly at `GET /api/v1/reference/cities` as plain strings (never OpenAPI enums). Localities are not part of this story.

## Boundaries & Constraints

**Always:** `apps.catalog` is added to `INSTALLED_APPS` with an `AppConfig` (label `catalog`); `City` has a UUID primary key, a display `name`, an `is_active` flag (default true) and creation time, with a case-insensitive unique constraint on the name (a `Lower("name")` unique constraint) and a name of 2 to 80 characters made only of letters, spaces, hyphens and apostrophes; the data migration seeds Karachi idempotently (re-running or an existing differently cased row never creates a duplicate and never overwrites an edited row); only `catalog.services` writes the table (the data migration is the only other writer) and it takes `actor` first: `create_city(actor, name)` requires an administrator actor (visitor `unauthenticated`, any other non-admin `not_found`), validates the name, writes the city and an audit record `city.created` (registered in `catalog`'s `AppConfig.ready` with an empty metadata schema, target type `city`, target id the city id) in the same transaction, and a duplicate name case-insensitively raises a clear `DomainError`-style validation error without writing; `catalog.selectors.list_cities()` returns plain immutable values (id and name), active cities only, ordered by name, never model instances; the endpoint `GET /api/v1/reference/cities` lives in `presentation/api`, declares `AllowPublic`, uses an explicit field-allowlist serializer, returns `{"items":[{"id":"<uuid>","name":"Karachi"}]}` with city names as plain strings in the OpenAPI schema (no enum), is unpaginated because the list is small and bounded, and is part of the regenerated `openapi.yaml` and authorisation matrix; the presentation layer never imports `catalog.models`; the runtime role receives its DML grants through the existing post-migrate hook; import-linter contracts (including the ban on other apps importing `catalog.models` and on HTTP imports in domain apps), the actor-first service test, the subscribe-location and admin-read convention tests and the freshness tests keep passing; comments only where intent is not obvious.

**Never:** add localities, areas, amenities or any other catalogue (later stories), an administrator endpoint or UI for cities (platform administration epic), caching, pagination or enums for city values, changes to the error envelope, or imports of `catalog.models` from outside `catalog`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Seed | after migrate on an empty database | exactly one city, Karachi, active | N/A |
| Re-seed | migration logic run again, or an existing `karachi` row | no duplicate, existing row unchanged | N/A |
| List | `GET /api/v1/reference/cities` as a Visitor | 200 `{"items":[{"id","name"}]}` with Karachi | N/A |
| Inactive city | a city with `is_active` false | omitted from the list | N/A |
| Ordering | several cities | ordered by name, case-insensitively stable | N/A |
| Contract | `openapi.yaml` | contains the route; `name` is `type: string` with no enum; response has only `id` and `name` | freshness test passes |
| Create, admin | administrator creates `Lahore` | city saved and `city.created` audit record in the same transaction | N/A |
| Create, others | visitor or non-admin user | `unauthenticated` or `not_found`, nothing written | N/A |
| Duplicate | `karachi` or ` KARACHI ` | refused, nothing written, no audit record | validation error |
| Bad name | blank, one character, over 80, digits or symbols | refused | validation error |
| Rolled back | create fails after the audit write | neither city nor audit record remains | N/A |
| Isolation | another app imports `catalog.models`; or presentation imports it | import-linter contract fails (existing contract) | N/A |
| Runtime role | `runtime` role | can read and write `catalog_city`, cannot run DDL | role test |

</frozen-after-approval>

## Code Map

- `apps/catalog/apps.py`, `apps/catalog/models.py`, `apps/catalog/migrations/0001_initial.py`, `0002_seed_karachi.py` -- app config (registers the audit action in `ready`), `City`, schema and idempotent seed
- `apps/catalog/services.py` -- `create_city(actor, name)`; `apps/catalog/selectors.py` -- `list_cities()` returning a small frozen dataclass; keep `events.py` and `subscribers.py` as they are
- `apps/presentation/api/reference.py` -- `CityView` (AllowPublic) and `CitySerializer` allowlist; `config/urls.py` -- route `api/v1/reference/cities`
- `config/settings/base.py` -- add `apps.catalog` to `INSTALLED_APPS`
- `openapi.yaml`, `docs/authorisation-matrix.md` -- regenerated
- `tests/catalog/` -- every matrix row; `tests/test_db_roles.py` -- runtime role on the new table
- `docs/reference-data.md`, `README.md` -- how reference values are managed, why they are strings, where localities arrive, how a later admin story manages cities

## Tasks & Acceptance

**Execution:**
- [x] `apps/catalog` app config, model, migrations and seed -- City table with Karachi
- [x] `apps/catalog/services.py`, `selectors.py` -- owner write path and read path
- [x] `apps/presentation/api/reference.py`, URL, settings, regenerated contracts -- public endpoint
- [x] `tests/catalog/`, role test -- cover every matrix row
- [ ] docs and README

**Acceptance Criteria:**
- Given the catalog app, when it is set up, then it owns a City table seeded with Karachi by a data migration and written only through catalog services.
- Given the public reference endpoint, when the frontend requests cities, then the list is returned from `/api/v1/reference/cities`, and city values are database-managed strings, never OpenAPI enums.
- Given administrator management of cities, then it arrives in the platform administration epic (this story adds only the service).

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | Real migration path that seeds Karachi never run by a test (VG, BH) | high | patch | A broken `RunPython` wiring would ship an empty city list with a green suite; MigrationExecutor test added. |
| 2 | IntegrityError race branch in create_city never exercised (VG) | medium | patch | Test forces the lost race. |
| 3 | Name handling: no NFC, whitespace runs, trailing separators, combining marks (EC, BH) | medium | patch | Normalised and validated with tests. |
| 4 | No selector to validate a stored city id (BH) | medium | patch | `get_city` added. |
| 5 | Presentation import check is a substring test; role probe row not cleaned up (EC, BH, VG) | low | patch | AST check with fixtures; try/finally cleanup. |
| 6 | Docs overstate the database charset rule, cite OQ-2 without a pointer (EC, BH) | low | patch | Corrected. |
| 7 | Database enforces only length, not charset (EC, BH, VG) | low | reject | Service is the single guard; documented. |
| 8 | Inactive duplicate gives a generic validation error with no reactivation path (EC, BH) | low | defer | Belongs to city management in the administration epic. |
| 9 | Import-linter contract for catalog models missing (BH) | false | reject | Exists since Story 1.2 for all eight domain apps. |
| 10 | Error responses on a public endpoint, no caching or throttle, unbounded list, Python vs Postgres lower, seed race (BH, EC) | low | reject | Per-operation errors already deferred; the list is small and bounded; fix adds complexity. |

## Design Notes

Decisions made without asking: `catalog` becomes the first installed domain app (it now owns a table); a minimal admin-only `create_city` service exists so AD-3's "written only through services" has a real write path and exercises the audit writer and `AppConfig.ready` registration pattern, while no endpoint exposes it; the list is unpaginated and not cached (tiny, bounded, public); the response wraps results in `items` to leave room for metadata; the frontend regenerates its client from the new `openapi.yaml` in its next frontend story; Karachi localities are open question OQ-2 and belong to a later story.

## Verification

**Commands:**
- `uv run python manage.py migrate --database=migrator` -- applies the catalog migrations
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest && uv run python manage.py makemigrations --check --dry-run` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- expected: both files regenerated, then committed
