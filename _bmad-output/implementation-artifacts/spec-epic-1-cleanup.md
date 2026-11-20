---
title: 'Epic 1 cleanup: split identity services, fix the agency-profile test flake'
type: 'refactor'
created: '2026-11-20'
status: 'in-progress'
route: 'dispatch'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-retro-2026-11-20.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

Retro action items 1 and 4 of the Epic 1 retrospective: (1) `apps/identity/services.py` is a 1,379-line module with 66 top-level definitions that every Epic 1 story grew; split it by area before Epic 2 adds more; (4) `tests/e2e/agency-profile.spec.ts` in the frontend flakes in full runs (three failures in three full runs, a different cities test each time, passing alone) and its serial mode hides the rest of the file after one failure; find and fix the root cause. Neither change may alter behaviour.

## Part A: identity services split (backend repository, branch `refactor/split-identity-services`)

**Always:**
- Behaviour, public function signatures, error codes, audit records, events, notifications and transaction boundaries stay exactly as they are; this is a pure move. `openapi.yaml`, `docs/authorisation-matrix.md` and every migration stay byte-identical (the freshness tests must pass without regenerating).
- Convert `apps/identity/services.py` into a package `apps/identity/services/` whose `__init__.py` re-exports every name the rest of the code (presentation views, subscribers, management commands, other apps, tests) uses today, so existing imports `from apps.identity import services` and `services.<name>` keep working unchanged.
- Split by area into sibling modules inside the package (suggested: `accounts` (creation, state changes: suspend, reactivate, deactivate, `_insert_account`, shared helpers and `_require_*` guards), `registration` (individual and agency, `_register`, `_notify_existing`), `verification` (email verification issue, confirm, request), `sessions_auth` (sign-in, sign-out, session ending and refresh, admin sign-in, the shared `_attempt`), `passwords` (reset request, confirm, change), `profile` (individual and agency profile read and update, email change request, issue, confirm), `admin` (provisioning, disable, enable, remove, reset)); pick the exact seams that minimise cross-module imports and avoid import cycles; shared private helpers live in one small internal module. No module over about 400 lines.
- Tests that monkeypatch module-level names in `apps.identity.services` (for example `make_password`, `ratelimit`, `notifications`, `audit`, `transaction`, `timezone`, `check_password`) must patch the module where the name is actually looked up after the move: update those patch targets in the tests (this is the only test change allowed, apart from nothing else); do not weaken any assertion.
- The import-linter contracts (13), ruff check and format, `makemigrations --check`, `spectacular --validate` (output unchanged) and the full pytest suite (2374 passed, 2 skipped expected) must all pass. The subscribe-location convention test and the actor-first service convention test keep passing and still see the moved functions (adjust the convention tests' discovery only if it assumed a single file, with the same strictness).
- Update `docs` that name `apps/identity/services.py` paths, and add a short section to `docs/architecture-notes.md` describing the new package layout.

**Never:** change behaviour, rename public functions, change a message, error code or transaction scope, regenerate `openapi.yaml`, add Claude attribution.

## Part B: agency-profile test flake (frontend repository, branch `fix/agency-profile-spec-flake`)

**Always:**
- Reproduce first: the failures only appeared in full-suite runs (about 13 to 20 minutes). Find a faster reproduction (for example `--repeat-each` on the file, running it while the machine is under CPU load, or with `--workers=1` plus other specs before it) and record what reproduces it and the root cause in the README testing notes.
- Fix the root cause in the test helpers or the stub (suspects: the `signIn` helper reaching `/sign-in` before hydration, shared stub state not reset between tests, assertions made before a request settled, serial mode chaining state); change application code only if the investigation proves a real application race and then add a test that pins it.
- Remove `test.describe.configure({ mode: "serial" })` (or equivalent) from `agency-profile.spec.ts` unless the tests genuinely share state; make each test independent by resetting the stub in `beforeEach`. If other specs share the same flake pattern (the `signIn` helper is used widely), fix the helper once.
- Prove the fix: the file passes 5 consecutive full-file runs and 3 runs under artificial CPU load, and one complete full-suite run passes in the background (about 20 minutes; never run two Playwright runs at once; the backend work in Part A does not use these ports).
- lint `--max-warnings 0`, typecheck, build and `api:check` pass; no new high-entropy literals (gitleaks).

**Never:** raise timeouts as the only fix; skip or delete tests; add Claude attribution.

## Verification

- Backend: `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run pytest -q && uv run python manage.py makemigrations --check --dry-run && uv run python manage.py spectacular --validate --file openapi.yaml` and `git diff --stat openapi.yaml docs/authorisation-matrix.md` empty.
- Frontend: as in Part B.

## Review Triage Log

## Design Notes

A pure refactor needs no new behaviour tests; its proof is the unchanged suite, unchanged generated contracts and the import-linter contracts. The flake fix needs a reproduction recipe so the root cause is demonstrated, not guessed.

</frozen-after-approval>
