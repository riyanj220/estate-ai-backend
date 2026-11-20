# Individual registration (Story 1.15, backend half)

`POST /api/v1/registrations/individuals` registers an Individual account. It is public (`AllowPublic`), JSON only,
and throttled by the seeded `registration` scope (10 per hour per client address, see `docs/rate-limits.md`; the 11th
request gets the 429 envelope with `Retry-After`). Invalid requests count against the limit too.

## Request and response

Body: `full_name`, `email`, `password`, `phone`, all required. Any other field (for example `role`) is refused with
`validation_error` and `fields.non_field_errors`. There is no buyer or seller choice.

A valid request always answers `202 {"status": "check_your_email"}`. The body carries no account, profile or user id.

## Enumeration rule

A new and an already-registered email (compared case-insensitively) get the same status, body and headers.
For an existing address nothing is created or audited; the address is sent a fixed "already registered" notice
(plain text, no account details, a link to `/sign-in` built with `email_link`) through the mail port after commit,
addressed to the stored email, not the typed case variant.
The password is hashed on both paths so timing does not reveal existence (`tests/identity/test_registration.py`
spies on the hasher). A lost race on the unique email index is treated as "already registered".
A duplicate phone is never reported; two accounts may share one.

### Existing-address branches

- Notices are capped per account with the seeded `verification_email` scope (5 per hour, keyed by account id, shared
  with Story 1.16's verification sends). Over the cap nothing is sent and the response is unchanged.
- An existing account that is still Email-unverified gets no sign-in notice; the service emits
  `identity.events.AccountVerificationRequested` (`account.verification_requested`, field `account_id`) instead.
  Story 1.16 subscribes to it and sends a new verification link (`docs/email-verification.md`).
- A verified (or otherwise non-unverified) account gets the sign-in notice.

## What a new registration writes (one transaction)

- `Account` in the Email-unverified state, email stored as entered, password hashed.
- `IndividualProfile` (`full_name`, `phone` as E.164; the phone is private-class and returned by no endpoint).
- Audit record `account.created` (actor kind `visitor`, metadata `account_type` only).
- Event `account.registered` (payload: account id only), dispatched after commit.

Phone, email, password and name never appear in logs, audit metadata, event payloads or error bodies.

## Field rules and codes

The rules are one pure set of validators in `apps/identity/validation.py` and `apps/identity/passwords.py`, used by
the service `identity.services.register_individual(actor, full_name, email, password, phone)` and by the API
serializer. Every failing field is reported at once, keyed by field name, with a static message.

| Code | Meaning |
| --- | --- |
| `required` | missing, null or blank |
| `invalid` | wrong JSON type |
| `invalid_name` | not 2 to 80 letters, spaces, hyphens, apostrophes, full stops (Unicode letters allowed), or contains a phone, email or address (`reject_contact_details`); NFC and whitespace collapsed before checking |
| `invalid_email` | malformed, over 254 characters, or a non-ASCII domain |
| `password_too_short` / `password_too_long` | under 8 or over 1024 characters |
| `password_too_common` | in Django's common-password list (`CommonPasswordValidator` only; the auth app is not installed) |
| `password_matches_email` | equals the email or its local part, case-insensitively |
| `invalid_phone` | not a Pakistani mobile or valid international number |

Phone shapes: `03xx-xxxxxxx` (any common separators), `+92 3xx xxxxxxx`, `0092 3xx xxxxxxx`, or `+` followed by 8 to 15
digits. Pakistani landlines are refused. The check and the E.164 normalisation are `normalise_phone` in
`apps/kernel/privacy/patterns.py`, the only place phone-shape logic may live (the repository test enforces it).

The service refuses any actor other than a Visitor with `forbidden` and writes nothing.

## Hand-off

- **Story 1.16 (email verification, done)** subscribes to `identity.events.AccountRegistered` (`account.registered`,
  field `account_id`) and sends the verification link; registration itself sends no verification email. See
  `docs/email-verification.md`.
- **Frontend half** is a separate spec written after this merges: regenerate the typed client from `openapi.yaml`
  (operation `register_individual`), show the field errors by code, the 429 message and the "check your email" state.

## Notes and accepted limits

- The view resolves the actor and refuses non-visitors (403 `forbidden`) before reading the body. Real signed-in
  sessions only resolve as such once Story 1.17 adds authentication; today every request is a Visitor.
- Terms and privacy-policy acceptance is not captured in this story (deferred, a product decision).
- Phone retention and erasure belong to the governance erasure use case (AD-23).
- `create_account` (administrator provisioning) now also rejects non-ASCII email domains, via the shared email check.
- Per-client throttling behind a reverse proxy depends on the trusted-proxy setting (`TRUSTED_PROXIES`, see the
  security docs); without it every request shares the proxy's address.
- The residual timing difference between branches beyond the password hash (queries, mail queueing) is accepted.
- Names may contain ZWNJ and ZWJ inside (not at the edges). `normalise_phone` refuses invisible and combining
  characters and treats a `00` prefix as `+`.

# Agency registration (Story 1.19, backend half)

`POST /api/v1/registrations/agencies` registers an Agency account. It is a sibling of the Individual endpoint: public
(`AllowPublic`, no authentication), JSON only, a signed-in caller gets `403 forbidden` before the body is read, and it
shares the `registration` throttle scope with Individuals (10 per hour per client across both endpoints, standard 429
with `Retry-After`). It answers the same `202 {"status": "check_your_email"}` whether or not the email is registered,
takes the same existing-address path (`_notify_existing`: a sign-in notice, or a new verification request for an
unverified account), hashes the password once on both paths, and uses the Story 1.16 verification email and link
unchanged (`docs/email-verification.md`). `identity.services.register_agency(actor, agency_name, email, password, phone,
office_address, city_id)` and `register_individual` share one internal helper.

## Fields and codes

Body, all mandatory strings, strict allowlist (an unknown field such as `account_type` or `verification_status` is
refused with `fields.non_field_errors = unknown_field` and nothing is created): `agency_name`, `email`, `phone`,
`office_address`, `city_id`, `password`. Every failing field is reported at once with a stable code.

| Field | Rule | Codes |
| --- | --- | --- |
| `agency_name` | 2 to 100 characters after NFC and whitespace collapse; letters, decimal digits (Unicode `Nd` only), combining marks (only directly after a letter or another mark), inner ZWNJ/ZWJ, spaces and `& , . - ' ’ ( ) /`; must contain a letter; passes `reject_contact_details` (it is shown publicly) | `required`, `invalid`, `invalid_name` |
| `email` | as Individuals (`check_email`); it is the business email and the account email | `required`, `invalid`, `invalid_email` |
| `phone` | as Individuals (`check_phone`), stored E.164 | `required`, `invalid`, `invalid_phone` |
| `office_address` | 5 to 300 characters after collapse; no control, format or invisible characters (NUL, newlines and tabs are refused, not collapsed); must contain at least 3 letters or decimal digits; inner ZWNJ/ZWJ are allowed. Private, so digits and phone-looking runs are allowed and there is no contact-pattern check | `required`, `invalid`, `invalid_address` |
| `city_id` | canonical UUID string of an active City (looked up through `catalog.selectors.get_city` via `identity.selectors.city_is_active`; identity never imports catalog models) | `required`, `invalid` (not a string or not a UUID), `invalid_city` (unknown or inactive) |
| `password` | as Individuals, checked against the email | `required`, `invalid`, `password_*` |

The validators are pure (`check_agency_name`, `check_office_address`, `check_city_id`, `validate_agency` in
`apps/identity/validation.py`); `validate_agency` takes the city lookup as an argument, so the serializer and the service
report the same errors. The city (database) lookup runs only after every other field passes, so `invalid_city` appears
once the rest is valid; the service re-checks the city inside the transaction, so a city deactivated meanwhile yields
`invalid_city` and nothing is created. `invalid_name` has one neutral message for both Individuals and Agencies.

## What a new registration writes (one transaction)

- `Account` of type `agency`, Email-unverified; audit `account.created` (metadata `account_type` only).
- `AgencyProfile` (`agency_name`, `business_phone`, `office_address`, `city_id`, `verification_status` = `unverified`, and `description`, optional public text that registration leaves empty and the profile route of Story 1.21 sets; see `docs/profile.md`).
  A CHECK constraint allows only `unverified` today; the verification-evidence story (Story 21) extends the set.
  `city_id` is a plain UUID column, not a foreign key into catalog.
- Event `account.registered` (account id only), dispatched after commit; Story 1.16 sends the verification email.

## Privacy classes

| Data | Class |
| --- | --- |
| `agency_name`, `city_id`, `description` | public (exposed by later profile stories; `agency_name` is in `GET /api/v1/me` for the owner) |
| `business_phone`, `office_address` | private: never in a public or registration response, error body, log record, audit metadata, event payload or email |
| business email | private (the account email, owner only) |

`GET /api/v1/me` (the actor summary) gains `agency_name` (empty for non-agency accounts) so the frontend header can name
the agency; no phone, address or city is added. Canary tests cover logs, audit, errors, events and the mail outbox.

## Classification and later concerns

There is no code-level private-data catalogue; the `AgencyProfile` docstring and this document are the classification.
An unexpected database error on the profile insert surfaces as a 500 envelope; only the exception class is logged
(never its message), so the phone and address cannot reach logs (covered by a test).
Agency-name uniqueness and lookalike checks are out of scope here and belong to a later story.

## Non-goal

Employees of one agency share the account. The system does not distinguish them: there are no per-employee seats,
sign-ins or audit trails.

## Notes

- The agency page of the frontend is a second spec written after this merges (operation `register_agency`).
- Contact-detail rejection applies to the public name only; the private address is stored as typed (collapsed).
