# Password reset and password change (Story 1.18, backend half)

## Endpoints

| Route | Who | Purpose |
| --- | --- | --- |
| `POST /api/v1/password-reset-requests` | public | `{"email"}`. Always `202 {"status": "check_your_email"}`. |
| `POST /api/v1/password-resets/confirm` | public | `{"token", "password"}`. `200 {"status": "password_reset"}` (`no-store`). |
| `POST /api/v1/me/password` | signed in, CSRF | `{"current_password", "new_password"}`. `200 {"status": "password_changed"}` (`no-store`). |

All three bodies are strict: any other field is `400 validation_error`. Confirm is POST so link prefetchers cannot consume
the token.

**Administrators are excluded (Story 1.22).** The request and the confirmation treat an administrator account as unknown:
the request answers the same `202` and sends nothing, and a confirm with any token of an administrator is `link_invalid`.
A forgotten administrator password is recovered only with `provision_admin reset-password` (`docs/administrators.md`).
A signed-in administrator can still change their own password at `POST /api/v1/me/password`, under the strong
administrator policy.

## Reset flow

1. The request emits `account.password_reset_requested` (account id only) for an existing account that is not
   Deactivated (Email-unverified, Active and Suspended may all reset). `identity.subscribers.send_password_reset_email`
   runs after commit, in a transaction of its own: it issues a token and queues a plain-text email (subject "Reset your
   EstateAI password", link `email_link("/reset-password", {"token": ...})`) through the mail port. Unknown and
   Deactivated accounts, and accounts whose password is not usable, do nothing (they are treated like unknown). A handler failure is logged by exception class only and never changes a response.
2. The frontend page confirms with the token and a new password.

### Tokens

`PasswordResetToken` (separate table from verification tokens, same helpers from `identity.verification`): SHA-256
`token_hash` (unique), `created_at`, `expires_at` (creation plus 1 hour), `used_at`. The plain token exists only in the
email link. Issuing locks the account row and marks every earlier unused reset token used, so only the newest link works.
Retention job `identity.password_reset_tokens` purges tokens used or expired more than 7 days ago.

### The 429 interpretation

The story says "more than 5 per hour per account returns 429". A 429 tied to one account would reveal that the account
exists, so:

- the 429 is **per client**: seeded policy `password_reset_request` (5 per hour, key kind `client`), answered with the
  standard envelope and `Retry-After` for every request alike, whatever the address;
- the **per-account** cap (seeded `password_reset_email`, 5 per hour) is silent: past it the response is the same `202` and
  nothing is sent.

Status, body and headers are identical for unknown, unverified, verified, suspended, deactivated, unusable-password and
capped addresses.

**Symmetric response-path work**: every string request does one lookup and one counter write in the shared
`password_reset_email` scope. An eligible account is keyed by the account; every other input (unknown, deactivated,
unusable, or an invalid-looking address) is keyed by an HMAC of the SHA-256 of the normalised address (key part
`address`), so no raw address is stored. The decision is silent either way. **Accepted residual**: the after-commit handler
and SMTP time still differ between a known and an unknown address; they are not part of the response (same as Story 1.16).

### Confirm

Limited per client: seeded policy `password_reset_confirm` (20 per hour, key kind `client`); over it the standard `429`
with `Retry-After`, whatever the token. Every answer of the confirm and change routes, errors included, carries
`Cache-Control: no-store`.

Order: well-formed token check, token lookup, then under locks (account, then token, like email verification) the
Deactivated, used and expiry checks, and only then password validation with the registration policy against the account
email. So a field error appears only for a valid link, and a validation failure does not consume the token (a typo does
not burn the link).

| Code | Status | When |
| --- | --- | --- |
| `link_invalid` | 400 | unknown, malformed, oversized, non-string token, or a Deactivated account |
| `link_expired` | 410 | past `expires_at` |
| `link_used` | 409 | already used, or replaced by a newer link (checked before expiry) |
| `validation_error` | 400 | valid link, bad password: `fields.password` is `required`, `password_too_short`, `password_too_long`, `password_matches_email`, `password_too_common` or `invalid` |

On success, in one transaction: the new hash is stored, the token is used and the account's other unused tokens are
superseded, **every** open session ends (reason `password_changed`, written directly in the transaction, so a crash before
the after-commit event cannot leave an old session alive), audit record `account.password_reset` is written (no
metadata), `account.session_ended` (reason `password_changed`) is emitted, and the "Your EstateAI password was changed"
notice is queued after commit (fixed copy, no password, no link; it says all sessions were signed out). A reset does not verify the email and does not change the
account state. The password hash cost is paid on success only.

## Change

Needs a session and `X-CSRFToken` (enforced by the authentication class: no session is `401 unauthenticated`, a missing
token `403 forbidden`). The service receives the caller's own session id from the authentication class (`request.auth`),
and refuses with `unauthenticated` unless it is an open session of the caller.

- Wrong current password: `400 validation_error`, `fields.current_password = "incorrect"`, nothing changes.
- Throttle: seeded policy `password_change_failed` (5 per 900 s, key kind `account`). Only wrong current passwords count.
  Once the cap is reached the same `incorrect` error is returned without checking, so the right password during the window
  also fails and the route never answers 429. Attempts per account run one at a time (the sign-in advisory-lock pattern), so
  parallel guesses cannot pass the check together; the failure count is committed even though the attempt fails. A success
  does not reset the counter.
- New password: registration policy against the account email (`fields.new_password` carries the policy code) and it must
  differ from the current one (`fields.new_password = "password_same_as_current"`). Neither counts as a failure.
- On success, in one transaction: hash replaced, every **other** open session ended (`password_changed`), audit record
  `account.password_changed` (no metadata), `account.session_ended` emitted with `except_session_id` (the handler ends all
  sessions of the account except that one), notice queued after commit (it says the other sessions were signed out). The current session stays signed in. A change also
marks every unused reset token of the account used, so an outstanding reset link cannot outlive it.

`AccountSessionEnded` gained the optional `except_session_id`; `identity.services.end_account_sessions` accepts the same
exclusion and `ended_before` (a datetime): the event handler then ends only sessions created up to that moment, so a session
opened after the commit is never ended by the delayed handler. Reset passes no exclusion, change passes the current session;
both pass the transaction's time as `ended_before`.

## Privacy

The token, its hash, passwords, hashes and the email never appear in logs, audit metadata, error bodies or event payloads;
the only place a token appears is the email link, and no email other than the reset link carries a link or password.

## Accepted limits

- A client can use up an account's silent per-account cap (5 an hour) by requesting resets for its address; the owner can
  retry after the hour.
- The change throttle is per account, so someone holding a
  signed-in session can delay the owner's change by burning the window; they cannot learn or change anything.
- Fixed-window burst: up to twice the limit can land across a window boundary.
- A client can request a link for someone else's address (the owner gets an unwanted email) and can use up the silent
  per-account cap, which also means a newer unwanted link supersedes one already emailed; the owner can retry after the hour
  (same as Story 1.16 triage).
- Known and unknown addresses still differ in after-commit handler and SMTP time (not in the response).

## Frontend hand-off

- `/reset-password`: read `token` from the query, remove it from the address bar (`history.replaceState`), send
  `Referrer-Policy: no-referrer`, never log or store it. Show a new-password form; on `validation_error` show
  `fields.password` and let the user retry with the same link; on `link_invalid`, `link_expired` and `link_used` offer a
  new reset request. After `200` send the user to sign in (all sessions ended, so any later `401` means signed out).
- Reset request form: always show "check your email" on `202`; `429` shows a try-again-later message using `Retry-After`.
- Change form (signed in): fetch the CSRF token, send `X-CSRFToken`; show `fields.current_password` and
  `fields.new_password`; stay signed in on `200`. Operations: `request_password_reset`, `confirm_password_reset`,
  `change_password`.
