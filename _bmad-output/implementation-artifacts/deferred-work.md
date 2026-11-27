
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

- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-rate-limit-foundation.md`
  summary: Add a peek and reset (or failed-only counting) approach so the sign-in limit counts failed attempts only.
  evidence: `hit` counts every call; Story 1.17 owns the sign-in scope and must decide how to count failures.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-domain-events-and-idempotency.md`
  summary: Delete expired idempotency records in bounded batches and add tests for the purge-between-insert-and-read retry branch.
  evidence: `purge_expired` deletes in one statement and the retry branch in `claim` is unexercised; settle with the retention runner (Story 1.9).

- source_spec: `_bmad-output/implementation-artifacts/spec-1-9-runtime-executor-flags-ai-log-and-retention.md`
  summary: Add an overlap guard (advisory lock), bounded-batch deletes, a `--job NAME` option and a summary line to `run_retention` when the schedule is decided at deployment.
  evidence: Each purge is one unbounded DELETE and overlapping scheduled runs are not prevented; documented as single-instance for now.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-10-audit-trail-and-reasoned-private-reads.md`
  summary: Write an audit record for feature-flag changes (register a `flag.changed` action with a token schema) and apply the Story 1.14 contact-pattern check to read reasons.
  evidence: `set_flag` is an administrator action recorded only by its changed-by and changed-at columns; read reasons currently use a crude `@` and digit-run rule.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-11-city-reference-data.md`
  summary: Give `create_city` field-level error detail and a reactivate path for an inactive city when city management is built.
  evidence: An inactive city still reserves its name and the service raises one generic `validation_error`; settle in the platform administration epic.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-15-individual-registration.md`
  summary: Capture terms and privacy-policy acceptance (with timestamp) at registration if the product requires it.
  evidence: Registration collects a name and private phone with no consent record; the PRD is silent, so it needs a product decision before launch.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-15-individual-registration.md`
  summary: Define retention and erasure for the private phone and register the profile with the private-read target registry when an administrator read of it exists.
  evidence: The phone is stored in plaintext in `identity_individualprofile`; erasure belongs to the governance use case (AD-23).

- source_spec: `_bmad-output/implementation-artifacts/spec-1-16-email-verification.md`
  summary: Add a per-client throttle policy for `POST /api/v1/email-verifications/confirm` if token-guessing abuse or database load appears.
  evidence: Confirm is public and unthrottled; tokens carry 256 bits of entropy so guessing is impractical, but each attempt costs a lookup.

- source_spec: `_bmad-output/implementation-artifacts/spec-5-4-provider-adapters.md`
  summary: Add the Groq LLM and Whisper adapter and re-run the trial when a `GROQ_API_KEY` exists.
  evidence: Story 5.4 asks for Groq and Gemini; only a Gemini key exists, so there is no second provider or comparison.
- source_spec: `_bmad-output/implementation-artifacts/spec-5-4-provider-adapters.md`
  summary: Before real user data reaches Gemini, decide on the paid tier and set `AI_DAILY_BUDGET` from the project's real limits.
  evidence: Unpaid-tier inputs may be used and human-reviewed and the terms forbid personal data; free-tier request limits are not published.
