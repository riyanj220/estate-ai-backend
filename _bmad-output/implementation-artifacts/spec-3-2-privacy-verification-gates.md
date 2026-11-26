---
title: 'Story 3.2: Privacy verification gates in CI (backend only)'
type: 'feature'
created: '2026-11-27'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Story 3.2)'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-21, AD-26)'
  - '{project-root}/docs/visibility.md'
  - '{project-root}/docs/privacy-patterns.md'
  - '{project-root}/docs/testing.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Privacy rules are enforced by individual tests; a new route could leak without anything noticing.

**Approach:** Three automated gates that walk the route list and the schema, so later endpoints are covered without new code: a schema walk, a canary sweep over every public and other-account-facing endpoint, and a log scan over that run. Tests only (plus whatever tiny helpers they need). Keep it simple.

## Boundaries & Constraints

**Always:**
- **Schema walk**: from the generated OpenAPI schema, classify each operation by who can see its response (public/AllowPublic, other-account-facing, owner-only, admin) using the existing authorisation matrix source, not a hand list; assert no private-class registry field name (and no phone-like property for public shapes) appears in any public or other-account-facing response schema, resolving `$ref`s recursively. A self-test proves that adding a private field to a public schema makes it fail. A route the classifier cannot classify fails the gate.
- **Canary sweep**: seed canary phone numbers, emails and private addresses in several formats (separators, "+92", spaces) into stored fields and private addresses (profiles, agency profile, listings' private address, coordinates); exercise every public and other-account-facing GET (and safe-to-call) endpoint enumerated from the route table (parameterised routes get ids from the seeded data); assert no canary (normalised: digits-only comparison for phones) in any response body or header. An endpoint added later without a way to build its URL fails the gate with a clear message (forces an entry).
- **Log scan**: capture all logs during the sweep (root logger, request logging) and assert no canary.
- Marked `guard` so `pytest -m guard` runs them; documented in docs/testing.md and docs/visibility.md. No gitleaks-triggering literals (fake low-entropy values; reserved domains).

**Never:** weaken an existing privacy test; add `.gitleaksignore` entries; add Claude attribution to commits.

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

## Verification

ruff, format, lint-imports, the new gates, `-m guard`, and one `pytest -n 3` full run.
