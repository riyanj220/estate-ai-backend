# Email verification (Story 1.16, backend half)

## Flow

Agency registration (Story 1.19) uses the same flow: it emits the same `account.registered` event, so an agency gets the
same verification email and link, and confirming it makes the Agency account Active; the agency profile is unchanged.

1. Registration emits `account.registered` (or, for a repeat registration of an unverified address,
   `account.verification_requested`). `identity.subscribers.send_verification_email` runs after commit, in a fresh
   transaction: it issues a token and queues the plain-text email (subject "Verify your EstateAI email address")
   through the mail port. The link is `email_link("/verify-email", {"token": ...})`, built from `FRONTEND_BASE_URL`.
2. The frontend page loads, then calls `POST /api/v1/email-verifications/confirm` with `{"token": "..."}`. POST, so
   link prefetchers and mail scanners cannot consume the link.
3. A Visitor who lost or expired the link calls `POST /api/v1/email-verification-requests` with `{"email": "..."}`.

## Tokens

`secrets.token_urlsafe(32)`, shown only in the email. `EmailVerificationToken` stores the SHA-256 hash (`token_hash`,
unique), `created_at`, `expires_at` (created plus 24 hours) and `used_at`. Issuing a token marks every earlier unused
token of that account as used, so only the newest link works (an older one answers `link_used`). Issuing locks the
account row, so concurrent issues serialise. Retention job `identity.email_verification_tokens` deletes tokens used or
expired more than 7 days ago (`run_retention`).

## Confirm

`200 {"status": "verified"}` marks the token used and calls the verification path in one transaction (lock order:
account, then token row with `select_for_update`, so concurrent confirmations give exactly one success). Failures use
the standard envelope:

| Code | Status | When |
| --- | --- | --- |
| `link_invalid` | 400 | unknown, malformed, empty, over 200 characters or non-string token |
| `link_expired` | 410 | past `expires_at` (the frontend offers a new link) |
| `link_used` | 409 | already used, or replaced by a newer link |

A wrong body shape (missing or extra field, not an object) is `validation_error`. An already verified account with a
fresh token succeeds idempotently with no second audit record. A Suspended or Deactivated account has its
verification recorded, but its derived state still wins. The only audit action is `account.email_verified`.

Precedence: a token that is both used (or superseded) and expired answers `link_used`; `link_used` is checked before
`link_expired`. Issuing is limited to accounts that are exactly Email-unverified, so a suspended unverified account
never gets a live link on any path. Confirming still works for a Suspended or Deactivated account (a link issued before
the change): the verification is recorded for the day the account is reactivated, and the derived state wins (FR-74).
Confirm ignores the supplied actor: it is a public, system-driven action and the audit record is written as `system`.
The confirm response carries `Cache-Control: no-store`.

## Resend and the enumeration rule

Always `202 {"status": "check_your_email"}` with the same body and headers whether the address is unknown, unverified,
verified, over its cap, suspended or deactivated. Everything observable happens after commit through events.

- Per client: seeded policy `verification_resend` (5 per hour, key kind `client`); the 6th request gets the standard
  429 with `Retry-After`, for any address alike, which is how the 429 discloses nothing.
- Per account: the shared `verification_email` cap (5 per hour); over it nothing is sent, silently. The registration
  email itself is not counted by this cap.
- Unverified account: `account.verification_requested`, handled as above. Verified account: the "already registered"
  sign-in notice (shared cap). Unknown, suspended or deactivated: nothing.

## Privacy

The token, its hash, the email address and the link never appear in logs, audit metadata, error bodies or event
payloads (events carry the account id only). A subscriber or mail failure is logged by exception class only and never
changes a response; the user can request a new link.

## Accepted limits

- An attacker can use up an account's per-account cap (5 an hour) by requesting links for its address; the owner can
  retry after the hour. Accepted for the prototype.
- Confirm has no throttle: tokens carry 256 bits of entropy, so guessing is infeasible. Revisit if abuse appears.

## Related: password reset

Password reset (Story 1.18) mirrors this design with its own table and 1-hour links; see `docs/password-reset.md`. The
two flows share only the token helpers in `identity.verification`.

## Hand-off

- **Frontend half** (separate spec): the `/verify-email` page reads `token` from the query, confirms on load, shows
  success and each failure code with a request-new-link form. The page must send `Referrer-Policy: no-referrer`,
  remove the token from the address bar after reading it (`history.replaceState`), confirm with a POST on load, and
  never log or store the token. Operations: `confirm_email_verification`,
  `request_email_verification`. Regenerate the typed client from `openapi.yaml`.
- **Story 1.17 (sign-in)** can rely on the Active state only after verification; Email-unverified accounts follow the
  capability matrix.
