---
title: 'Story 1.3: API contract, error envelope and structured logging'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'e631115d668cea4d80e41d102759f2d8798adf57'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-2-layered-app-skeleton-and-import-contracts.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** There is no API contract, no uniform error shape and no safe request logging, so the frontend cannot rely on typed endpoints and any endpoint could leak private values through errors or logs.

**Approach:** Add DRF and drf-spectacular under `/api/v1/`, a stable error-code catalogue with one error envelope, a cursor-pagination class, a committed `openapi.yaml` guarded by a freshness test, and request-ID middleware with allow-listed JSON logs, each proven by tests.

## Boundaries & Constraints

**Always:** the error code catalogue lives in `apps/kernel` (pure Python); DRF exception handler, serializers, pagination and schema hooks live in `apps/presentation/api`; the import-linter contracts from Story 1.2 keep passing; serializers are explicit allowlists; error bodies and logs never contain submitted values; a request ID is generated per request (never trusted from the client) and returned in the `X-Request-ID` response header; code comments only where intent is not obvious.

**Never:** add models or migrations; add authentication, permission or throttle behaviour (Stories 1.6, 1.7, 1.17); add business endpoints; change the Story 1.1 health view or database settings; log request bodies, query strings, headers, cookies or exception messages.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Validation error | invalid body containing a phone, email and address | 400 envelope, `code` `validation_error`, `fields` maps field name to static catalogue messages | submitted values never echoed (including invalid-choice messages) |
| Known errors | not found, method not allowed, unauthenticated, forbidden, stale revision, idempotency key reused, throttled | status 404, 405, 401, 403, 409, 422, 429 with the matching catalogue `code` | 429 adds `Retry-After` when known |
| Unhandled exception | view raises an arbitrary exception | 500 envelope `server_error`, generic message | exception class name only is logged |
| Request ID | any response | `request_id` in the envelope equals the `X-Request-ID` header and the log line | client-supplied ID ignored |
| Request log | request with private values in body and query string | one JSON line with only allow-listed fields | none of the values appear anywhere in captured log output |
| Pagination | test endpoint over more rows than one page | deterministic order, `next` and `previous` cursors, stable across pages | invalid cursor gives 404 envelope |
| Contract | committed `openapi.yaml` differs from generated | freshness test fails | message says to regenerate |
| Catalogue | schema | all codes published as an enum in `openapi.yaml`; each operation lists the default error responses | adding a code changes the schema |

</frozen-after-approval>

## Code Map

- `pyproject.toml`, `uv.lock` -- add djangorestframework, drf-spectacular (+ PyYAML if needed)
- `apps/kernel/errors.py` -- catalogue: code, HTTP status, default message; helper to look them up
- `apps/kernel/request_logging.py` -- request-ID middleware (generate UUID4, set header, store on request) and JSON log formatter with the field allow-list (timestamp, level, request_id, method, route template, status, duration_ms, exception class); no body, query, headers or exception messages
- `apps/presentation/api/{exceptions,serializers,pagination,schema}.py` -- exception handler producing the envelope with static messages keyed by ErrorDetail code; `ErrorEnvelopeSerializer` with explicit fields; cursor pagination (ordering required, page size 20, max 100); spectacular hooks adding the `ErrorEnvelope` and error-code enum components and default error responses
- `config/settings/base.py`, `config/urls.py` -- INSTALLED_APPS (`rest_framework`, `drf_spectacular`), REST_FRAMEWORK (JSON only, no authentication classes, `UNAUTHENTICATED_USER = None` because the auth app is not installed), SPECTACULAR_SETTINGS, LOGGING (JSON to stdout; silence `django.request` and Django server loggers that log paths), middleware, schema route `/api/v1/schema`
- `openapi.yaml` -- generated with `manage.py spectacular --validate --file openapi.yaml`, committed
- `tests/api/` -- tests-only urlconf with views raising each error and a cursor-paginated list over `ContentType`; envelope, scrub, request-ID, log, pagination and openapi freshness tests
- `README.md` -- how to regenerate `openapi.yaml`

## Tasks & Acceptance

**Execution:**
- [x] `pyproject.toml`, `uv.lock` -- add DRF and drf-spectacular -- stack per AD-16
- [x] `apps/kernel/errors.py`, `apps/presentation/api/exceptions.py`, `serializers.py` -- catalogue, envelope, scrubbing -- uniform errors
- [x] `apps/kernel/request_logging.py`, `config/settings/base.py` -- middleware, allow-listed JSON logging -- AD-26
- [x] `apps/presentation/api/pagination.py`, `schema.py`, `config/urls.py`, `openapi.yaml` -- pagination, schema hooks, committed contract
- [x] `tests/api/` -- cover every matrix row; README regeneration note

**Acceptance Criteria:**
- Given the backend under `/api/v1/`, when the contract is generated, then `openapi.yaml` is committed and a test fails when it is stale.
- Given any error, when the API responds, then the body is `{error:{code,message,fields,request_id}}` with a catalogued code, and a test proves submitted phone, email and address values never appear in it.
- Given any request, when it is logged, then the line is structured JSON with `request_id` and only allow-listed fields, and a middleware-level test proves submitted private values never reach the log output.
- Given a list endpoint using the shared cursor pagination, when paged, then results are deterministic.
- Given the error catalogue, when the schema is read, then every code is published and a new code requires a schema change.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | `fields` keys echo submitted dict keys (BH, EC) | high | patch | DictField/JSONField child keys became `fields` keys; now top-level declared name only, hostile-key test added. |
| 2 | Empty validation detail gives empty `fields` (EC) | low | patch | Falls back to `non_field_errors`. |
| 3 | Async middleware path untested (BH, VG) | medium | patch | Only the sync client ran; AsyncClient test added. |
| 4 | Real urlconf handlers unverified; 400/403 and SuspiciousOperation not enveloped (BH, EC, VG) | medium | patch | handler400/403 added, SuspiciousOperation mapped, default-urlconf 404 test added. |
| 5 | 406/415 untested; tautology test (VG, BH) | low | patch | Tests added; tautology deleted. |
| 6 | Non-request log lines undiagnosable; method token logged verbatim (BH, EC) | medium | patch | Exception class from exc_info added; method restricted to standard verbs. |
| 7 | Default `django` logger may emit host/path values (EC) | low | patch | Routed to JSON handler only. |
| 8 | Multi-field cursor ordering skips or repeats rows (EC, BH) | medium | patch | Rejects orderings that are not exactly one field. |
| 9 | pyyaml runtime dep; spectacular unbounded (BH) | low | patch | Moved to dev; bounded. |
| 10 | Public schema route, no default permission (BH) | low | patch | README note; gating belongs to 1.4/1.6. |
| 11 | Per-operation error statuses not overridable; 401 lacks WWW-Authenticate and is unreachable without authenticators (BH, EC) | medium | defer | Needs real endpoints and authentication (1.6, 1.17). |
| 12 | `ErrorBody` schema hand-written beside serializer (BH, EC, VG) | low | defer | Drift only on future edits; single-source later. |
| 13 | openapi.yaml and uv.lock missing from diff (BH, EC) | false | reject | Diff excluded them; both exist and tests pass. |
| 14 | Spec status stale (BH) | false | reject | Transient mid-workflow state. |
| 15 | Status taken from catalogue, health probes logged, empty request_id, DomainError in plain views, setdefault vs overwrite, exception escaping middleware (BH, EC) | low | reject | Unlikely in everyday use; fix adds branches. |
| 16 | cursor_ordering untested; cursor exposes sort value (VG, BH) | low | reject | Ordering fixed to one safe field by convention. |

## Design Notes

Decisions made without asking: error codes are a published enum (reference values stay strings, as agreed in Epic 1 context); validation messages are static per DRF error code because default DRF messages can echo submitted values; the cursor-pagination test uses `ContentType` rows so no model is added; the request-ID middleware lives in `kernel` because kernel owns structured logging (AD table) and uses no DRF; Daphne's access log stays off (it records paths and query strings); freshness is a test run by CI, not a separate workflow step.

## Verification

**Commands:**
- `uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && git diff --exit-code openapi.yaml` -- expected: no diff
