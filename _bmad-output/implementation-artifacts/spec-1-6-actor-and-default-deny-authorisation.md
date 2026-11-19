---
title: 'Story 1.6: Actor and default-deny authorisation foundation'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '06cae42e3c8f74683ac03f3b0f6f3070571dd545'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-3-api-contract-error-envelope-and-structured-logging.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing yet says who is calling. A forgotten permission declaration would leave an endpoint open, and services could read ambient request state or leak that a resource exists.

**Approach:** Add an immutable `Actor` in the kernel, one resolver in the presentation layer that turns a request into an Actor (a Visitor until sign-in exists), DRF default-deny with explicit public, signed-in and admin permission classes, a test that fails any route without an explicit declaration, a convention test that service functions take `actor` first, and a generated authorisation matrix document.

## Boundaries & Constraints

**Always:** `Actor` lives in `apps/kernel/actor.py`, is frozen, pure Python (no Django, DRF or request import) and has kind `visitor`, `user`, `admin` or `system`, an optional `account_id` (UUID, required for user and admin, absent for visitor and system) and a `request_id`; one resolver in `apps/presentation/api` is the only code that reads a request to build an Actor, and it fails closed to Visitor on anything unrecognised; DRF `DEFAULT_PERMISSION_CLASSES` is a deny-all class; every route declares its permission explicitly on the view; a Visitor reaching a sign-in-required route gets `unauthenticated` (401 envelope), a signed-in non-admin reaching an admin route and any unauthorised access to a resource get `not_found` (404 envelope, no existence leak); service-layer helpers raise the existing `DomainError` codes; import-linter contracts keep passing and a new contract forbids `django.http`, `rest_framework` and `channels` in the domain apps (not kernel or presentation); comments only where intent is not obvious.

**Never:** add the Account model, sessions, authentication, sign-in or any real capability matrix (Stories 1.12 and 1.17); add models or migrations; let a service read a request, a session or thread-local state; trust any client-supplied header or value to set an Actor; change the error envelope or log format.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Resolver, no identity | any request | Visitor Actor with the request id | N/A |
| Resolver, identity present | request carries an authenticated identity (kind user or admin, account id) set by trusted code | matching Actor | an unknown kind, missing or non-UUID account id gives Visitor (fail closed) |
| Actor construction | user or admin without account id; visitor or system with one | construction fails | `ValueError` |
| Undeclared permission | route whose view declares no explicit permission | route-permission test fails naming the route | N/A |
| Default deny | view with no explicit permission called | 403 envelope | N/A |
| Visitor on sign-in route | Visitor requests a signed-in route | 401 `unauthenticated` envelope | N/A |
| Non-admin on admin route | signed-in user requests an admin route | 404 `not_found` envelope | Visitor gets 401 |
| Hidden resource | unauthorised caller requests a resource | 404 `not_found`, same body as a missing resource | N/A |
| Sample service | called with a non-owner or Visitor actor | refuses with `DomainError`; first parameter is `actor` | N/A |
| Service convention | public function in `apps/*/services.py` without `actor` first | convention test fails (proven by a failing fixture) | N/A |
| Domain app imports HTTP code | domain app imports `django.http`, `rest_framework` or `channels` | import-linter contract fails (proven by a failing fixture) | N/A |
| Matrix | route list | `docs/authorisation-matrix.md` lists every route with its declared permission; a test fails when it is stale | N/A |

</frozen-after-approval>

## Code Map

- `apps/kernel/actor.py` -- `ActorKind`, frozen `Actor` with validated constructors, small helpers `require_signed_in(actor)` and `require_admin(actor)` raising `DomainError("unauthenticated" or "not_found")`
- `apps/presentation/api/actors.py` -- `resolve_actor(request)` reading a trusted `request.authenticated_identity` (to be set by Story 1.17's authentication) and the request id the Story 1.3 middleware stores; `request.actor` attached by a small DRF mixin or middleware used by views
- `apps/presentation/api/permissions.py` -- `DenyByDefault`, `AllowPublic`, `RequireSignedIn`, `RequireAdmin`
- `apps/presentation/api/authz_matrix.py` -- pure function that walks the URL configuration and returns the route table; `scripts/authz_matrix.py` -- writes `docs/authorisation-matrix.md`
- `config/settings/base.py`, `config/urls.py` -- `DEFAULT_PERMISSION_CLASSES`; declare `AllowPublic` on the health and schema views
- `pyproject.toml` -- forbidden-imports contract for domain apps
- `tests/authz/` -- tests-only urlconf (public, signed-in, admin, hidden-resource and undeclared views), resolver, permission, route-permission (real urlconf plus failing fixture), sample service in `tests/authz/sample_service.py`, matrix freshness; `tests/architecture/` -- service-signature convention test with failing fixture and the HTTP-import fixture
- `docs/authorisation-matrix.md` -- generated table plus a short hand-written section on how later phases extend it
- `README.md` -- one paragraph on the authorisation rules

## Tasks & Acceptance

**Execution:**
- [x] `apps/kernel/actor.py` -- Actor model and helpers -- AD-22
- [x] `apps/presentation/api/actors.py`, `permissions.py`, settings, URL declarations -- resolver and default deny
- [x] `apps/presentation/api/authz_matrix.py`, `scripts/authz_matrix.py`, `docs/authorisation-matrix.md` -- generated matrix
- [x] `pyproject.toml`, `tests/architecture/` -- forbidden HTTP-import contract and service-signature convention with failing fixtures
- [x] `tests/authz/` -- cover every matrix row; README paragraph

**Acceptance Criteria:**
- Given any request, when it reaches a view, then one resolver produces an Actor with kind, account id and request id, and no service reads ambient request state.
- Given a route without an explicit permission declaration, when the route-permission test runs over the full route list, then it fails, so DRF's default is deny and every route declares its permission.
- Given a resource the caller may not know about, when an unauthorised caller requests it, then the API returns not-found, and a sign-in-required endpoint returns unauthenticated to a Visitor.
- Given a sample mutating service, when I call it, then it takes `actor` first and enforces policy itself, and a documented authorisation test matrix exists that later phases extend.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | Matrix generator crashes on composed permissions; label spoofing; get_permissions override passes as explicit (BH, EC) | high | patch | Route test could give a false pass; explicitness now by class identity, composed and overridden views flagged. |
| 2 | render_document mishandles missing markers (BH, EC) | medium | patch | Raises a clear error. |
| 3 | UUID leniency and nil UUID resolve to a signed-in actor (BH, EC, VG) | medium | patch | Canonical lowercase form only; nil fails closed. |
| 4 | System actor behaviour unpinned (VG, BH, EC) | medium | patch | Tested and documented: passes signed-in, refused by admin-only. |
| 5 | Matrix doc contradicts sample service for administrators (BH) | medium | patch | Doc corrected, system row added. |
| 6 | No HTTP-level test of the mutating route; undeclared route tested with admin only (BH) | medium | patch | Added. |
| 7 | HTTP-import ban misses sibling Django modules; new domain app can skip it (BH, EC) | medium | patch | Modules added; completeness test extended. |
| 8 | README over-claims that tests enforce "services never read request state" (EC) | medium | patch | Reworded to what is enforced. |
| 9 | Kernel import test is cwd-dependent; errors.py unchecked (BH) | low | patch | Fixed. |
| 10 | Schema-route gating dropped from README (BH) | low | patch | Restored and tracked in deferred-work. |
| 11 | service-signature test scans only services.py (VG, BH, EC) | low | defer | No such modules exist yet; widen with the first domain service. |
| 12 | Actor not in request logs or denial audit (BH) | low | defer | Belongs with the audit trail (Story 1.10). |
| 13 | Visitor gets 401 on admin route, so existence is not hidden (BH) | low | reject | Intended by the spec: sign-in-required returns unauthenticated; wording corrected. |
| 14 | request.actor only set when a permission runs (EC) | low | reject | Views use get_actor(request); documented. |
| 15 | WWW-Authenticate, OPTIONS preflight, viewset methods, decorator-wrapped services (EC) | low | reject | Same-origin proxy, no such views; fix adds complexity. |

## Design Notes

Decisions made without asking: the resolver reads a trusted `request.authenticated_identity` that does not exist until Story 1.17 sets it, so today every request is a Visitor and the resolver is proven with tests; admin routes hide their existence from signed-in non-admins; because no authentication classes exist, DRF would raise 403 instead of 401, so `RequireSignedIn` raises the unauthenticated error itself; the authorisation matrix is generated from the route list (AD-21) with a freshness test, like `openapi.yaml`; the sample service lives in tests so no domain code is invented; kernel is excluded from the HTTP-import contract because it hosts the middleware.

## Verification

**Commands:**
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && git diff --exit-code openapi.yaml` -- expected: no diff
