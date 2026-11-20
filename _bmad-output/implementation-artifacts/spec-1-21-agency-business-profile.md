---
title: 'Story 1.21: Agency business profile (backend half)'
type: 'feature'
created: '2026-11-20'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-19-agency-registration.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-20-individual-profile-management.md'
  - '{project-root}/docs/registration.md'
  - '{project-root}/docs/profile.md'
  - '{project-root}/docs/privacy-patterns.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An Agency account can register but cannot see or correct its business details, and there is no description field for the later public agency profile.

**Approach:** Add a signed-in Agency business profile read and update (name, business phone, office address, city and a new description), beside the Individual profile of Story 1.20 and built the same way: the Agency account alone can read and edit it, the business phone and full office address stay private, the agency name, description and city are the public-class fields for a later public profile, and the contact-detail check rejects phone numbers, emails and addresses in the public text fields with an explanation. The email change of Story 1.20 already works for Agency accounts and is reused (the profile shows `email` and `pending_email`). This spec is the backend half; the frontend page is a second spec after the backend merges.

## Boundaries & Constraints

**Always:**
- **Model**: `AgencyProfile` gains `description` (CharField 1000, blank, default `""`, PUBLIC class), one migration; update the model docstring privacy classes (`agency_name`, `description`, `city_id` public; `business_phone`, `office_address` private). Written only by `identity.services`; runtime role DML already covers the table.
- **Read** `GET /api/v1/me/agency-profile`, `RequireSignedIn`, Agency accounts only (Individual and Administrator sessions get `403 forbidden`), `Cache-Control: no-store`. Returns `{"agency_name","description","city_id","business_phone","office_address","email","pending_email","verification_status"}`: `business_phone` and `office_address` are the owner's own private values, returned only here; `email` is the sign-in address; `pending_email` is computed exactly as in Story 1.20 (any accepted, unused, unexpired email-change request, independent of whether a link was sent); `verification_status` is the read-only Agency Verification status (`unverified` today). Nothing else; never in the actor summary.
- **Update** `PATCH /api/v1/me/agency-profile`, same guards plus CSRF (existing authentication class), strict body: only `agency_name`, `description`, `city_id`, `business_phone`, `office_address` allowed, at least one present, unknown fields refused (`400 validation_error`, `unknown_field`), `email` and `verification_status` are NOT updatable here (`unknown_field`). The account type is checked BEFORE the body is validated (an Individual or Admin gets `403`). Validation reuses the Story 1.19 validators (`check_agency_name`, `check_phone`, `check_office_address`, `check_city_id` with the active-city lookup through `catalog.selectors`, never importing catalog models): `agency_name` `required`/`invalid`/`invalid_name`; `business_phone` `required`/`invalid`/`invalid_phone` (mandatory: blank or null is `required`, never a way to remove it); `office_address` `required`/`invalid`/`invalid_address` (mandatory); `city_id` `required`/`invalid`/`invalid_city` (mandatory; city lookup only after the other fields pass, and re-checked inside the transaction); `description` OPTIONAL: blank or null clears it to `""`, otherwise 1 to 1000 characters after NFC and whitespace collapse (paragraph breaks collapse to single spaces), printable, no control characters or NUL, must contain a letter or digit, and must pass the shared public-text check `reject_contact_details` → new codes `invalid_description` (shape) and `contains_contact_details` (phone numbers, email addresses or street addresses found) with static explanatory messages added to the catalogue ("Descriptions cannot contain phone numbers, email addresses or street addresses." and a neutral invalid-description message). The agency name keeps `invalid_name`, whose message already says no phone numbers or email addresses. All failing fields are reported at once with stable codes, values never echoed. Success `200` with the same body as the read, `no-store`; one audit record `account.profile_updated` (existing action, empty metadata) only when a stored value actually changed; an unchanged submission writes nothing. Updates run under the account row lock (account, then profile, like Story 1.20). A Suspended agency may update (same as Individuals); a Deactivated one has no session.
- The email change request and confirm routes of Story 1.20 stay as they are and keep working for Agency accounts; add tests proving an Agency can request and confirm an email change and that `pending_email` shows in the agency profile.
- **Privacy**: the phone, the address, the new email and tokens never appear in logs, audit metadata, errors, events (ids only) or emails; only the owner's own profile response carries the phone and address; the actor summary is unchanged except it already carries `agency_name`. A representation of the profile view dataclass must not expose the private fields (`repr=False`). Canary tests over logs, audit, events and errors.
- **Docs**: extend `docs/profile.md` with an Agency section (routes, fields, privacy classes, the description rule and codes, the email change reuse, what the frontend must do), update `docs/registration.md` (the description field exists, optional, public) and `README.md`; `openapi.yaml` and `docs/authorisation-matrix.md` regenerated. All earlier tests, convention tests and import-linter contracts keep passing. Comments only where intent is not obvious.

**Never:** return the business phone or office address to anyone but the owner or outside the agency profile response; let the profile route change the email, the verification status or the account type; let a blank phone, address, name or city clear a mandatory field; accept an inactive or unknown city; skip the contact-detail check on the agency name or the description; import `apps.catalog.models` from identity; log or audit private values; return 429 from this route; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Read | signed-in Agency | 200 all eight fields, `no-store` | N/A |
| Read, wrong type | Individual or Admin session | 403 `forbidden` | envelope |
| Read, no session | | 401 `unauthenticated` | envelope |
| Update name | valid `agency_name` | 200 new body; audit `account.profile_updated`; other fields untouched | N/A |
| Update phone, address, city | valid values, active city | 200; phone stored E.164 | N/A |
| Update description | valid text | 200; collapsed whitespace stored | N/A |
| Clear description | blank or null | 200; stored `""` | N/A |
| Description with contact details | phone number, email, street address | 400 `description: contains_contact_details` with the explanation message | envelope |
| Name with contact details | phone or email in the name | 400 `agency_name: invalid_name` | envelope |
| Mandatory fields | blank or null name, phone, address, city | 400 `required`; nothing changes | envelope |
| Invalid values | bad phone, address, wrong types | stable codes, all at once | envelope |
| Invalid city | unknown or inactive id | 400 `city_id: invalid_city` | envelope |
| City deactivated mid-request | between validation and write | `invalid_city`, nothing changes | N/A |
| Unchanged submission | same values | 200, no audit record | N/A |
| Unknown or forbidden field | `email`, `verification_status`, `account_type`, anything else | 400 `unknown_field`; nothing changes | envelope |
| Empty body | `{}` | 400 validation error | envelope |
| Wrong type, invalid body | Individual or Admin with a bad body | 403 (type check first) | envelope |
| No CSRF | PATCH without a token | 403 `forbidden` | envelope |
| Agency email change | agency requests and confirms an email change | works as for Individuals; `pending_email` visible in the agency profile | N/A |
| Suspended agency | | may read and update | N/A |
| Concurrent updates | parallel PATCHes | serialised, one audit record per real change | threaded test |
| Privacy | logs, audit metadata, events, errors | no phone, no address, no email or token (canary) | test |
| Storage | profile row | description collapsed, phone E.164, address collapsed, city id | test |
| Actor summary | `/me` for an agency | unchanged: no phone, no address, no description | test |
| Contract | `openapi.yaml` | routes, body, new codes and messages, freshness passes | test |

</frozen-after-approval>

## Code Map

- `apps/identity/models.py`, `migrations/0008_agency_description.py` -- `description`
- `apps/identity/validation.py` (description validator, reuse the rest), `services.py` (get and update agency profile), `selectors.py` (agency profile view), `apps/kernel/errors.py` (codes and messages)
- `apps/presentation/api/profile.py` (agency views and serializers), `config/urls.py`
- `openapi.yaml`, `docs/authorisation-matrix.md` regenerated
- `tests/identity/test_agency_profile.py`, `tests/api/test_agency_profile_api.py` -- every matrix row
- `docs/profile.md`, `docs/registration.md`, `README.md`

## Tasks & Acceptance

**Execution:**
- [ ] description field, validator and codes
- [ ] agency profile read and update services, audit, row lock
- [ ] endpoints, regenerated contracts
- [ ] tests covering every matrix row; docs

**Acceptance Criteria:**
- Given I am signed in as an Agency account, when I update name, contact details, address or description, then the changes are saved and only the Agency account can edit them.
- Given the profile fields, when classification is applied, then the business phone and full office address are private, while name, description and city are public fields for the later public profile.
- Given the Agency name and description, when they are saved, then the contact-detail check rejects phone numbers and addresses with an explanation.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, backend half (blind, edge-case, verification-gap).

| # | Finding | Verdict | Route |
|---|---------|---------|-------|
| 1 | An agency whose stored city was later deactivated could not save any edit that resent that city (edge) | medium | patch: the city is looked up only when it differs from the stored one |
| 2 | The in-transaction city recheck does not lock the City row, so a concurrent deactivation can still slip through (edge, blind) | low | patch: docs and docstring say so; no lock added |
| 3 | A direct service call by an Individual with an invalid body got `validation_error` instead of `forbidden` (verification-gap) | low | patch: type and state checked before the body, plus a light `require_agency` check for the view |
| 4 | Description rules: non-strings used the generic code, NEL/VT/FF were refused, orphan combining marks were accepted, dead length check (edge, blind) | low | patch |
| 5 | Schema declared mandatory fields nullable (blind) | low | patch |
| 6 | Contact-detail heuristics miss phones split by words or commas, long separator runs, "info at agency.com", Urdu digit words and Plot/Block/Sector addresses (blind) | medium | defer: shared patterns from Story 1.14, recorded as known gaps in `docs/privacy-patterns.md` and `docs/profile.md` |
| 7 | Tests: agency `pending_email` for an existing address and expiry, NUL and joiner descriptions through HTTP (verification-gap) | medium | patch |
| 8 | Suspended agencies can still edit public name and description (blind) | low | defer: consistent with Individuals |
| 9 | Row-lock test is a spy plus parallel run, not a proof; Deactivated agency covered at service level only; agency name keeps `invalid_name` rather than the description's code (verification-gap, blind) | low | defer |

Deviation recorded: the view keeps a light `require_agency` pre-check (one query, no profile view) because the serializer judges the body shape first.

## Design Notes

Decisions made without asking: the Agency profile is a sibling route of the Individual one so each account type has one clear shape; "contact details" means the business phone (the email is the account email and changes through the verified flow of Story 1.20); the description is optional so an agency can clear it but name, phone, address and city stay mandatory; the description collapses whitespace so it stays a single paragraph until a richer format is decided; the verification status is shown read-only because it is owned by the later verification story; the agency name uniqueness question stays deferred to that story.

## Verification

**Commands (backend):**
- `uv run python manage.py migrate --database=migrator`
- `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run pytest -q && uv run python manage.py makemigrations --check --dry-run`
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- regenerate and commit
