---
title: 'Story 1.15: Individual registration (backend half)'
type: 'feature'
created: '2026-11-19'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'e7d821e2bb67ab41ebcc70c23b347d8a2d15b0db'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/planning-artifacts/prds/prd-estate-ai-backend-2026-11-18/prd.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-14-shared-contact-detail-patterns.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Visitor cannot yet create an account. This is the first [BE+FE] story; this spec is the backend half (the frontend registration page is a second spec written after the backend merges and regenerates `openapi.yaml`).

**Approach:** Add `POST /api/v1/registrations/individuals` that registers an Individual account (full name, email, password, phone, all mandatory) in the Email-unverified state with an individual profile and no role choice, validates every field with static field-level messages, answers identically whether or not the email already exists (an existing address gets an "already registered" notice by email), keeps the phone private, and is throttled by the seeded `registration` limit.

## Boundaries & Constraints

**Always:** the endpoint is `POST /api/v1/registrations/individuals`, public (`AllowPublic`), rate-limited with the `registration` scope through the existing `PolicyThrottle` (429 envelope with `Retry-After`), JSON only, and returns the same `202` body `{"status":"check_your_email"}` for a new and an existing address; `identity` gains an `IndividualProfile` model (UUID primary key, one-to-one with `Account`, `full_name`, `phone` stored normalised as E.164, created and updated times; the phone is private-class data); the service `identity.services.register_individual(actor, full_name, email, password, phone)` takes the Visitor actor first, refuses any other actor kind with the existing refusal rules, runs entirely in one transaction and never exposes whether the email exists; field rules are one pure set of validators in `identity` that return error codes per field, used by both the service and the API serializer: full name 2 to 80 characters of letters, spaces, hyphens, apostrophes and full stops (Unicode letters allowed), normalised to NFC with whitespace collapsed, and free of contact details by calling `reject_contact_details`; email through the existing registration email checks (case preserved as entered, uniqueness case-insensitive, at most 254 characters); password at least 8 and at most 1024 characters, not in Django's common-password list (use `django.contrib.auth.password_validation.CommonPasswordValidator`, importing only that module, never installing the auth app), and not equal to the email address or its local part, case-insensitively; phone must be a valid Pakistani mobile (`03xx-xxxxxxx` in any common formatting, `+92 3xx xxxxxxx`, `0092 3xx xxxxxxx`) or a valid international number (`+` country code and 8 to 15 digits in total), normalised to E.164, and the validation and normalisation code lives in `apps/kernel/privacy/patterns.py` as one public function (the repository rule allows no other contact-data regex or digit-shape logic elsewhere, and its tests are extended with phone-validation catch and refuse cases); field errors use stable codes (`required`, `invalid_name`, `invalid_email`, `password_too_short`, `password_too_long`, `password_too_common`, `password_matches_email`, `invalid_phone`) with fixed static messages added to `FIELD_MESSAGES`, are keyed by field name in the existing error envelope, and never echo submitted values; a new account is created through the existing audited path (`account.created`, state Email-unverified) with its profile in the same transaction and the identity event `account.registered` (fields account id) is emitted for Story 1.16 to send the verification message; an existing email (case-insensitive) creates nothing and queues an "already registered" notice through the kernel mail port to that address (plain text, fixed copy, no account details, link to the sign-in page built with `email_link`), and the response and its status code are identical to the new-address case; the password is hashed in both branches so timing does not reveal existence (a test proves the hasher is called in both); two accounts may share a phone and a duplicate phone is never reported; the phone, email and password never appear in any response body, log record, audit metadata, event payload other than the account id, or error body (canary tests); the Visitor actor is the only caller (signed-in or admin accounts get the standard refusal); a request with an unknown or extra field is rejected with the standard validation error; `openapi.yaml` and the authorisation matrix are regenerated; all earlier tests, convention tests and import-linter contracts keep passing; comments only where intent is not obvious.

**Never:** build verification links, sign-in, sessions, password reset or the frontend page; create a buyer or seller role field; return account, profile or user ids to the registrant; disclose uniqueness anywhere (status code, body, headers, timing branches, mail to the registrant); send the verification email (Story 1.16); store the plain password; add models beyond `IndividualProfile`; add a contact-data regex outside `patterns.py`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| New registration | valid name, new email, password, phone | 202 `{"status":"check_your_email"}`; Email-unverified account, individual profile with E.164 phone, `account.created` audit, `account.registered` event after commit | N/A |
| Existing email | same email in another case | identical 202 body and status; no account, profile or audit written; an "already registered" notice queued to that address; password still hashed | N/A |
| Missing field | any of the four absent or blank | 400 envelope, `fields` has that field with code `required` | static messages |
| Bad name | too short, too long, digits or symbols, contains a phone or email | 400 `invalid_name` | never echoes the value |
| Bad email | malformed, over 254 characters | 400 `invalid_email` | N/A |
| Bad password | under 8, over 1024, common (`password123`, `12345678`), equals email or its local part | 400 with `password_too_short`, `password_too_long`, `password_too_common` or `password_matches_email` | N/A |
| Phone shapes | `0300-1234567`, `0300 1234567`, `03001234567`, `+92 300 1234567`, `0092 300 1234567`, `+44 20 7946 0958` | accepted, stored as `+923001234567` or the E.164 form | N/A |
| Bad phone | landline-looking Pakistani number, too short, letters, `+` with under 8 digits, over 15 digits | 400 `invalid_phone` | N/A |
| Shared phone | two registrations with the same phone | both succeed, nothing reported | N/A |
| Several errors | several fields invalid | one response lists every failing field | N/A |
| Unknown field | extra or role-like field such as `role` | 400 validation error | N/A |
| Rate limit | 11th request in an hour from one client | 429 envelope with `Retry-After`; counters keyed by client address | N/A |
| Non-visitor | signed-in or admin actor | standard refusal, nothing written | N/A |
| Privacy | log output, audit metadata, error bodies, event payloads | no phone, email, password or name | canary test |
| Enumeration | compare new and existing responses | same status, body and headers apart from request id | test |
| Timing branch | spy on the password hasher | called once in both branches | test |
| Contract | `openapi.yaml` | contains the route, request fields and the two response shapes, error codes unchanged | freshness test |
| Repository rule | phone-validation code outside `patterns.py` | repository test fails | N/A |
| Rollback | failure after account creation | no account, profile or audit remains | N/A |

</frozen-after-approval>

## Code Map

- `apps/kernel/privacy/patterns.py` -- add `normalise_phone(text)` returning an E.164 string or `None` (Pakistani mobile shapes and international numbers) and its tests in `tests/privacy/`; extend the repository rule's allowance accordingly (none outside this module)
- `apps/identity/models.py`, `migrations/0002_individual_profile.py` -- `IndividualProfile`
- `apps/identity/validation.py` -- pure field validators returning error codes; `apps/identity/passwords.py` -- password policy
- `apps/identity/services.py` -- `register_individual`; `apps/identity/events.py` -- `AccountRegistered`; `apps/identity/apps.py` -- register nothing new unless an audit action is needed
- `apps/identity/notices.py` (or inside services) -- the "already registered" message text and send
- `apps/kernel/errors.py` -- new `FIELD_MESSAGES` entries
- `apps/presentation/api/registrations.py` -- serializer with strict fields and the view with `rate_limit_scope = "registration"`; `config/urls.py` -- route
- `openapi.yaml`, `docs/authorisation-matrix.md` -- regenerated
- `tests/identity/test_registration.py`, `tests/privacy/test_patterns.py`, `tests/api/`, `tests/test_db_roles.py` -- every matrix row
- `docs/registration.md`, `README.md` -- endpoint behaviour, enumeration rule, field codes, hand-off note for Story 1.16 (the `account.registered` event) and for the frontend half

## Tasks & Acceptance

**Execution:**
- [x] phone normalisation in `patterns.py` with tests
- [x] `IndividualProfile`, validators, password policy
- [x] `register_individual` service, event, existing-address notice
- [x] endpoint, throttle, field messages, regenerated contracts
- [x] tests covering every matrix row, docs

**Acceptance Criteria:**
- Given the registration request, when a field is missing or invalid, then the response lists field-level errors, with the password at least 8 characters and not commonly used, and the phone a valid Pakistani mobile or international format.
- Given valid details, when I submit, then an Individual account is created in the Email-unverified state with an individual profile, no role choice is asked, and the phone is stored privately and appears in no response to anyone.
- Given an email that is already registered, when someone submits it, then the response is identical to a new registration and the address receives an "already registered" notice, with two accounts allowed to share a phone and no duplicate reported.
- Given repeated submissions, when the limit is exceeded, then the standard 429 envelope is returned.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, backend half (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | The existing-address notice can mail-bomb a victim from many client addresses; goes to the typed case variant (BH, EC) | high | patch | Only the per-client limit applied; now capped per account with the `verification_email` scope, sent to the stored address, response unchanged when capped. |
| 2 | An existing Email-unverified account gets only a sign-in notice and is stuck (EC) | medium | patch | New `account.verification_requested` event for Story 1.16. |
| 3 | Non-visitor with an invalid body gets validation detail; positional serializer mapping (EC) | medium | patch | Actor check first, keyword arguments. |
| 4 | ZWNJ and ZWJ rejected in names; phones with invisible characters accepted silently; `00` dialling prefixes refused (EC) | medium | patch | Fixed with tests. |
| 5 | Unscoped UPDATE and LIKE delete in the role probe; overstated test name (BH, VG) | low | patch | Scoped and renamed. |
| 6 | Terms acceptance, phone retention and erasure, admin provisioning tightening, proxy-dependent throttling, residual timing (BH, EC) | low | patch (docs) | Recorded as accepted or deferred. |
| 7 | Terms and privacy-policy acceptance not captured (BH) | medium | defer | Product decision; recorded in deferred-work. |
| 8 | Phone erasure, retention and private-target registration (BH) | medium | defer | Governance erasure use case (AD-23). |
| 9 | Thin OpenAPI schema (no lengths or formats); password checks against name and phone; IDN emails refused; duplicate validation path; messages "missing" (BH, EC) | low | reject | Fix adds complexity or the claim is false (`invalid` and `not_a_dict` messages already exist). |
| 10 | Signed-in refusal unreachable until authentication exists; residual timing; text-based repository test (EC) | low | reject | Documented; Story 1.17 resolves real actors. |

## Design Notes

Decisions made without asking: `202 Accepted` with a fixed body for both branches (nothing was created that the caller can use yet); the verification message itself is Story 1.16's job, triggered by the `account.registered` event, so this story only sends the existing-address notice; password hashing runs in both branches and the notice goes through the after-commit mail port so response timing does not depend on existence; the profile holds the full name and phone only (further profile fields belong to Story 1.20); the phone is normalised to E.164 so later uniqueness-free lookups and display masking are consistent; field errors live in the presentation serializer using the pure domain validators so the rules stay in `identity`; the frontend half is a separate spec written after this merges because the typed client must be regenerated from the new contract.

## Verification

**Commands:**
- `uv run python manage.py migrate --database=migrator` -- applies the identity migration
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest && uv run python manage.py makemigrations --check --dry-run` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- expected: files regenerated and committed
