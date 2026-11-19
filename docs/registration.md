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
