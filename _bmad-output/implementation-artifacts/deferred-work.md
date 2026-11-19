
- source_spec: `_bmad-output/implementation-artifacts/spec-1-3-api-contract-error-envelope-and-structured-logging.md`
  summary: Make the per-operation error response set overridable so endpoints publish 409, 422 and other catalogued statuses.
  evidence: Only 400, 401, 403, 404, 429 and 500 are stamped on every operation; revisit with the first endpoint that raises stale_revision or idempotency_key_reused.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-3-api-contract-error-envelope-and-structured-logging.md`
  summary: Add WWW-Authenticate on 401 and make `unauthenticated` reachable once authentication classes exist.
  evidence: With no authenticators DRF raises 403 for anonymous users; settle in Stories 1.6 and 1.17.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-3-api-contract-error-envelope-and-structured-logging.md`
  summary: Generate the ErrorBody schema component from the serializer instead of a hand-written copy.
  evidence: Only top-level property names are compared today; types and required flags could drift.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-actor-and-default-deny-authorisation.md`
  summary: Gate `/api/v1/schema` before any non-local deployment (disable it or require the Administrator capability).
  evidence: The route is public by design today; settle with the admin API (Story 1.22) or the deployment story (Epic 11).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-actor-and-default-deny-authorisation.md`
  summary: Widen the actor-first service-signature test to `services/` packages, selectors and class-based services.
  evidence: It scans only `apps/*/services.py`; widen when the first domain service lands.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-actor-and-default-deny-authorisation.md`
  summary: Record actor kind and account id in audit and denial records.
  evidence: Request logs carry no actor; the audit trail arrives in Story 1.10.
