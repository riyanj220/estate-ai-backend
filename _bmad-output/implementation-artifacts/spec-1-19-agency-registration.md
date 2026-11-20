---
title: 'Story 1.19: Agency registration (backend half)'
type: 'feature'
created: '2026-11-20'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-15-individual-registration.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-16-email-verification.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-11-city-reference-data.md'
  - '{project-root}/docs/registration.md'
  - '{project-root}/docs/privacy-patterns.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Only Individuals can register. An agency has no way to create a business account with its own identity, so it cannot later hold agency-owned listings or a publishing allowance.

**Approach:** Add a dedicated Agency registration endpoint beside the Individual one. It collects agency name, business email, business phone, office address, city (from the City reference list) and a password, creates an Agency account in the Email-unverified state with an Agency Verification status of "unverified", and reuses the Individual flow's generic duplicate-email response, rate limit, timing guard and Story 1.16 verification email. This spec is the backend half; the frontend page is a second spec after the backend merges.

## Boundaries & Constraints

**Always:**
- **Endpoint** `POST /api/v1/registrations/agencies`, public (`AllowPublic`, `authentication_classes = []`), a signed-in caller is refused before the body is read (`forbidden`, like Individuals). Body, all mandatory strings, strict allowlist, unknown field refused: `agency_name`, `email`, `phone`, `office_address`, `city_id`, `password`. Every failing field is reported at once as `400 validation_error` with `fields.<name>` = a stable code, values never echoed. Success is the same `202 {"status":"check_your_email"}` as Individuals, identical whether or not the email is already registered (an existing address gets the existing "already registered" notice or a new verification request through the shared `_notify_existing` path, never a different response). Same throttle scope `registration` (shared with Individuals, 10 per hour per client, standard 429 with `Retry-After`).
- **Validation** lives in `identity/validation.py` as pure functions with stable codes: `agency_name` 2 to 100 characters after NFC and whitespace collapse, letters, digits, spaces and `& , . - ' ’ ( ) /` only, must contain a letter, and passes the shared public-text check `reject_contact_details` (it will be shown publicly) → `required` or `invalid_name`; `email` via `check_email`; `phone` via `check_phone` (E.164, private); `password` via `check_password` against the email; `office_address` 5 to 300 characters after collapse, printable, no control characters or NUL, must contain a letter or digit → `required` or `invalid_address` (it is private, so digits and phone-looking runs are allowed; no contact-pattern rejection); `city_id` a UUID of an ACTIVE city (resolved through `catalog.selectors.get_city`, never importing catalog models) → `required`, `invalid` (wrong type or not a UUID) or `invalid_city` (unknown or inactive). A missing, null, blank or wrong-typed value is `required` or `invalid` as for Individuals.
- **Model** `AgencyProfile` in `identity` (UUID pk, OneToOne `Account` CASCADE related_name `agency_profile`, `agency_name` CharField 100 PUBLIC, `business_phone` CharField 16 PRIVATE E.164, `office_address` CharField 300 PRIVATE, `city_id` UUIDField PUBLIC (no foreign key into `catalog` models, validated in the service), `verification_status` CharField default `unverified` with a CHECK constraint allowing exactly `unverified` for now (Story 21 extends the set), `created_at`, `updated_at`), one migration. The business email is the account email. Written only by `identity.services`; the runtime role gets DML through the existing hook.
- **Service** `identity.services.register_agency(actor, agency_name, email, password, phone, office_address, city_id)` actor-first, Visitor only; validates through the pure validators plus the city lookup, hashes the password on BOTH the new and the existing-email path (timing guard identical to `register_individual`), then in one transaction inserts the account (type `agency`, audit `account.created` with `account_type` metadata, already enumerated), creates the `AgencyProfile`, and emits `account.registered` (events carry only the account id); an existing email takes the shared `_notify_existing` path. Refactor the common part with `register_individual` into one internal helper rather than duplicating it; all existing Individual tests keep passing unchanged. The Story 1.16 handler sends the same verification email for agencies.
- **Privacy**: the business phone and office address are private-class data: never returned by any public or registration response, never in an error body, log record, audit metadata, event payload or email; only `agency_name` and the city are public-class (exposed by later profile stories). Add a canary test over logs, audit, errors, events and the mail outbox for the phone and the address. Register the new fields in whatever private-data catalogue or classification list `kernel/privacy` or `docs/privacy-patterns.md` keeps for `IndividualProfile.phone` so the privacy convention tests cover them.
- **Actor summary** (`GET /api/v1/me`, `identity.selectors.get_actor_summary`): add `agency_name` (string, empty for non-agency accounts) so the frontend can show a display name; nothing private is added (no phone, no address). `openapi.yaml` regenerated.
- **Non-goal, documented**: employees of one agency share the account; the system does not distinguish them and there are no per-employee seats. Write it in `docs/registration.md`.
- Docs: `docs/registration.md` gains an Agency section (fields, codes, privacy classes, the generic response, rate limit, non-goal), the Story 1.16 doc notes agencies use the same flow, `docs/authorisation-matrix.md` and `openapi.yaml` regenerated, README updated. All earlier tests, convention tests and import-linter contracts keep passing. Comments only where intent is not obvious.

**Never:** return or log the business phone or office address anywhere; reveal whether an email is registered (status, body, headers, 429, timing); allow a signed-in caller to register; import `apps.catalog.models` from identity; let the client choose the account type, verification status or any other field not in the allowlist; accept an inactive or unknown city; add a contact-data regex outside `patterns.py`; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid registration | all six fields valid, active city, new email | 202 `check_your_email`; Agency account Email-unverified; `AgencyProfile` with status `unverified`; one `account.created` audit (type agency); `account.registered` event; verification email queued after commit | N/A |
| Existing email | any registered address | identical 202; no second account; existing-address notice or new verification request (as Individuals) | N/A |
| Enumeration | compare new vs existing vs capped | same status, body and headers apart from request id | test |
| Missing fields | any of the six absent, null or blank | 400 with `required` for each failing field, all at once | envelope |
| Wrong types | non-string values, non-UUID city, list or object | `invalid` per field | envelope |
| Agency name | digits only, contact details, over 100, bad characters | `invalid_name` | envelope |
| Phone | not a valid Pakistani mobile or international number | `invalid_phone` | envelope |
| Address | under 5, over 300, control characters, no letter or digit | `invalid_address` | envelope |
| City | unknown UUID, inactive city | `invalid_city` | envelope |
| Password | weak, common, equals email | the policy code on `password` | envelope |
| Unknown field | `account_type`, `verification_status`, `role` in the body | 400 `unknown_field`; nothing created | envelope |
| Signed-in caller | any session | 403 `forbidden` before the body is read | envelope |
| Rate limit | 11th registration (any type) from one client in an hour | 429 with `Retry-After`; the scope is shared with Individuals | envelope |
| Timing guard | new vs existing email | one password hash on both paths | test counts hasher calls |
| Verification flow | confirm the emailed link | agency account becomes Active, agency profile unchanged | N/A |
| Sign-in | verified agency signs in | `/me` shows `account_type: agency`, `agency_name`, no phone or address | N/A |
| Privacy | logs, audit metadata, errors, events, mail outbox | no business phone, no office address (canary) | test |
| Storage | profile row | phone E.164, address as typed (collapsed), city id, status `unverified` | test |
| Constraint | direct insert of another status value | rejected by the CHECK | test |
| Rollback | failure after account insert | no account, no profile, no event | N/A |
| Contract | `openapi.yaml` | route, field list, `agency_name` in the actor summary, freshness passes | test |

</frozen-after-approval>

## Code Map

- `apps/identity/models.py`, `migrations/0006_agency_profile.py` -- `AgencyProfile`
- `apps/identity/validation.py` -- agency validators and `validate_agency`; `apps/identity/services.py` -- `register_agency`, shared helper; `apps/identity/selectors.py` -- `get_actor_summary` gains `agency_name`
- `apps/presentation/api/registrations.py` -- agency serializer and view; `config/urls.py`
- privacy catalogue entries; `openapi.yaml`, `docs/authorisation-matrix.md` regenerated
- `tests/identity/test_agency_registration.py`, `tests/api/test_agency_registration_api.py` -- every matrix row
- `docs/registration.md`, `README.md`

## Tasks & Acceptance

**Execution:**
- [ ] validators, model, migration
- [ ] `register_agency` with shared helper, actor summary field
- [ ] endpoint, serializer, contract regeneration
- [ ] tests covering every matrix row; docs

**Acceptance Criteria:**
- Given the Agency form, when I submit agency name, business email, business phone, office address, city and credentials, then all are mandatory, invalid input returns field-level errors, and city is chosen from the City reference list.
- Given valid details, when I submit, then an Agency account is created with Agency Verification status "unverified", sharing the email verification flow, with the business phone and office address stored privately and the city public.
- Given several employees of one agency, when they use the account, then the system does not distinguish them (documented non-goal).
- Given the Agency endpoint, when it is used, then it applies the same rate limit and the same generic duplicate-email response as Individual registration.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, backend half (blind, edge-case, verification-gap).

| # | Finding | Verdict | Route |
|---|---------|---------|-------|
| 1 | Office addresses with an inner ZWNJ or ZWJ were rejected by a stray `isprintable()` (blind, verification-gap) | high | patch; inner-joiner tests added |
| 2 | City could be deactivated between validation and profile insert (edge) | medium | patch: re-check inside the transaction |
| 3 | City lookup ran on every attempt before cheaper checks (edge) | low | patch: lookup only after the other fields pass (so `invalid_city` is the one error not reported alongside others) |
| 4 | `/me` ran an extra profile query for every account and could show an agency name for a non-agency account (blind, edge) | medium | patch: query only the matching profile |
| 5 | Name accepted any Unicode alphanumeric and stray marks; address accepted "1----" (edge) | low | patch: Nd digits, marks after letters, at least 3 alphanumerics |
| 6 | `invalid_name` message claimed 2 to 80 letters (verification-gap) | low | patch: neutral wording |
| 7 | A database error could carry private values into logs (blind) | low | verified: handler logs exception class only; test forces a DataError and checks logs and the 500 body |
| 8 | Tests: canary covered one event type; capped responses compared by status only; Individual email used by an agency untested (verification-gap) | medium | patch |
| 9 | No code catalogue of private fields exists, so convention tests do not cover them (edge, verification-gap) | low | defer: model docstring and `docs/registration.md` are the classification |
| 10 | Duplicate public agency names, lookalikes (blind) | low | defer to the Agency verification story, documented |
| 11 | Validation runs twice (serializer then service), API-level timing test, cascade role probes, `city_id` has no index or FK (blind, edge, verification-gap) | low | defer, same design as Individual registration |

## Design Notes

Decisions made without asking: the Agency endpoint is a sibling of the Individual one and shares the rate-limit scope, the generic response, the timing guard and the verification flow through one internal helper; `city_id` is a plain UUID checked through the catalog selector so identity never imports catalog models; the verification status is a CHECK-constrained column that allows only "unverified" until the verification-evidence story extends it; the office address is private so it only needs sanity checks, not contact-pattern rejection, while the public agency name does; `/me` gains `agency_name` so the frontend header can name an agency without learning anything private.

## Verification

**Commands (backend):**
- `uv run python manage.py migrate --database=migrator`
- `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run pytest -q && uv run python manage.py makemigrations --check --dry-run`
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- regenerate and commit
