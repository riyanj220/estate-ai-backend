# Individual profile and email change (Story 1.20, backend half)

## Endpoints

| Route | Who | Purpose |
| --- | --- | --- |
| `GET /api/v1/me/profile` | signed in, Individual only | `{"full_name","phone","email","pending_email"}` (`no-store`). |
| `PATCH /api/v1/me/profile` | signed in, Individual only, CSRF | `{"full_name"?,"phone"?}`, at least one. `200` with the same body as the read. |
| `POST /api/v1/me/email-change` | signed in (Individual or Agency, not Admin), CSRF | `{"new_email","current_password"}`. Always `202 {"status":"check_your_email"}`. |
| `POST /api/v1/email-changes/confirm` | public | `{"token"}`. `200 {"status":"email_changed"}` (`no-store`). |

Any other account type on the profile routes is `403 forbidden` (Agency profile management is Story 1.21); an
Administrator on the email change is `403 forbidden`. No session is `401 unauthenticated`, a missing CSRF token on
PATCH/POST `403 forbidden`. Every answer of these routes, errors included, carries `Cache-Control: no-store`. All bodies
are strict: an unknown field is `400 validation_error` (`non_field_errors`: `unknown_field`).

## Privacy classes

- `phone` (E.164) is private. It is returned only by `GET` and `PATCH /api/v1/me/profile`, to its owner. `GET /api/v1/me`
  (the actor summary) never carries it.
- The current and the new email address are private. The new address is a recipient of its confirmation email and
  appears nowhere else; neither address nor the phone is ever in a log, an audit record, an event or an error body.
  Events carry ids only. Audit records have no metadata.
- Tokens: only the SHA-256 hash is stored; the plain token exists only in the emailed link.

## Profile read and update

`pending_email` is the address of the newest accepted email-change request that is still unused and unexpired, or `null`.
It is deliberately independent of whether a link was sent: free, taken, same-address, capped and failed-handler requests
all show it identically, so the profile read cannot tell a free address from a registered one. `link_issued` only decides
which row the handler supersedes and which row confirm accepts (an older issued link keeps working after a later request
that issued none, even though `pending_email` then shows the newer address).

`PATCH` accepts only `full_name` and `phone`. Both use the registration validators (`check_full_name`, `check_phone`), so
the phone stays mandatory: a blank or `null` phone is `fields.phone = required`, never a way to remove it. All failing
fields are reported at once with stable codes (`required`, `invalid`, `invalid_name`, `invalid_phone`), values are never
echoed, and nothing is saved if any field fails. `email` is not updatable here. The update runs under the account row
lock. It writes audit record `account.profile_updated` (no metadata) only when a stored value actually changed; an
unchanged submission (compared after normalisation) is `200` and writes nothing.

## Email change flow

1. **Request.** The body needs the account password. It is checked with Django's `check_password` under the SAME failed
   attempt throttle and advisory lock as the password change (`password_change_failed`, 5 per 900 s per account,
   silent): a wrong or capped current password is `400 validation_error` with `fields.current_password = incorrect`, never
   a 429 from that throttle, so the two routes cannot probe the password separately. A malformed new address is
   `fields.new_email = invalid_email` (also `required`, `invalid`); it does not count as a failure. The route is also
   limited by seeded policy `email_change_request` (5 per hour per account) with the standard `429` and `Retry-After`; it
   is the owner's own counter and reveals nothing about any address. Only requests with the RIGHT password count: a wrong
   or capped password never spends the budget and (while locked out) keeps returning the silent `incorrect`.
2. On acceptance the request, in one transaction, inserts an `EmailChangeToken` row (the typed address, a **throwaway**
   hash nobody knows, `link_issued = false`) and emits
   `account.email_change_requested` (account id and the row's id). It does no lookup of the new address. The answer is
   always `202 {"status":"check_your_email"}`.
3. **Handler** (`identity.subscribers.send_email_change_mail`, after commit, own transaction, failures logged by exception
   class only, never changing the response). Under the account then row locks it always queues the heads-up notice to
   the CURRENT address (a change was requested, nothing changes until confirmed, change your password if it was not you;
   no new address in it), in EVERY branch, so the owner's inbox cannot tell the branches apart. It then decides:
   - the new address is free: a real token replaces the row's throwaway hash (24 hour lifetime restarts), the row is
     marked `link_issued`, only THEN are the account's earlier issued, unused links superseded, and the confirmation email
     goes to the NEW address (link `/confirm-email-change?token=...`, "works once and expires in 24 hours");
   - the new address belongs to another account (compared in the database, case-insensitively) that is Email-unverified
     or Active: that address gets the existing "already registered" notice (a sign-in link, never a token); a Suspended
     or Deactivated owner is not mailed;
   - the new address equals the current one, or a silent cap applies, or the row is used, expired or already issued:
     nothing but the heads-up.
   A request that issues no link never touches the owner's valid pending link. Mail to the new/other address counts
   against two silent `verification_email` caps (5 per hour): per requesting account, and per target address (an HMAC of
   the lowercased address), so many throwaway accounts cannot mail-bomb one address. The heads-up is never capped (the
   verified-request budget bounds it).
4. **Confirm.** Public (`authentication_classes = []`: no CSRF, cookies ignored), POST so prefetchers cannot consume the
   link, limited per client by seeded policy `email_change_confirm` (20 per hour, key kind `client`; standard `429`).
   Only `link_issued` rows are accepted. Under locks (account, then token, as in the other confirmations): well-formed, known, unused, unexpired, account not
   Deactivated, and the new address STILL free case-insensitively. In one transaction it sets the new address, marks the
   token used, supersedes other unused change tokens, voids the account's outstanding verification and password reset
   links (they were mailed to the old address), verifies the email if it was not (`account.email_verified`, state rules
   unchanged), writes `account.email_changed` (no metadata) and queues, after commit, a fixed notice to the OLD address
   (no new address in it). Sessions are NOT ended: the request already needed the password, and the old-address notice
   is the owner's revert signal ("contact support", change your password).

A password change, a password reset and a deactivation also mark every unused change link used, so a link mailed before
them (possibly by someone who briefly held a session) dies with them.

The old address stays the sign-in address, and keeps working, until the link is used; the new address cannot sign in before.

| Code | Status | When |
| --- | --- | --- |
| `link_invalid` | 400 | unknown, malformed or non-string token; Deactivated account; the address was taken meanwhile (no reason given); lost a race on the unique index |
| `link_expired` | 410 | past `expires_at` |
| `link_used` | 409 | already used, or superseded by a newer request |

### Enumeration

Free, registered-to-another, equal-to-current and unusable new addresses answer the identical `202` (status, body, headers
apart from the request id): the response path performs the same work in every branch (password check, supersede, one
insert, one event) and never looks the address up; the effects happen after commit. `pending_email` is set for every
accepted request, so the profile read does not distinguish them either. The 429s depend only on the owner's own counters.
The only residual difference is the after-commit handler and SMTP time, which is not part of the response.

### Concurrency

The password check and the row creation run under the per-account advisory lock (same key as the password change). Two
accounts racing for one address are decided by the unique case-insensitive email index: the loser's `IntegrityError`
becomes `link_invalid`, never a 500. Parallel confirms of one link yield exactly one `200`, the rest `link_used`.

## Tokens and retention

`EmailChangeToken` (UUID pk, account FK cascade, `new_email` as typed and trimmed, unique SHA-256 `token_hash`,
`created_at`, `expires_at` = 24 hours, `used_at`); helpers from `identity.verification`. Retention job
`identity.email_change_tokens` purges rows used or expired more than 7 days ago.

## Accepted limits

- A row whose link was never issued (taken or unchanged address, capped, or a failed handler) is inert until it expires;
  the owner can simply request again.
- Suspended accounts may read and edit their profile and change their email, and confirm a link (nothing in the story
  restricts them; a Deactivated account has no session and its links are `link_invalid`).
- A held session with the password can use up the owner's five verified requests in an hour; it learns and changes
  nothing the owner would not be told about (every accepted request mails the heads-up).
- Timing: the response path does the same work in every branch, but the after-commit handler and SMTP time still differ
  (accepted, as in Stories 1.16 and 1.18).
- Fixed-window burst: up to twice a limit can land across a window boundary.

## Frontend hand-off

- Profile page (Individual only): `GET /api/v1/me/profile`; edit with `PATCH` (`X-CSRFToken`), show `fields.full_name` and
  `fields.phone`; the phone cannot be cleared. Show `pending_email` as "waiting for confirmation of ...".
- Change-email form: needs the password; show `fields.current_password` and `fields.new_email`; on `202` always say
  "check your inbox", never whether the address is registered; `429` uses `Retry-After`.
- `/confirm-email-change`: read `token` from the query, remove it from the address bar (`history.replaceState`), send
  `Referrer-Policy: no-referrer`, never log or store it, POST it; on `200` refresh `/api/v1/me` and the profile; on
  `link_invalid`, `link_expired`, `link_used` offer a new request. Operations: `get_profile`, `update_profile`,
  `request_email_change`, `confirm_email_change`.
