# Individual profile and email change (Story 1.20, backend half)

## Endpoints

| Route | Who | Purpose |
| --- | --- | --- |
| `GET /api/v1/me/profile` | signed in, Individual only | `{"full_name","phone","email","pending_email"}` (`no-store`). |
| `PATCH /api/v1/me/profile` | signed in, Individual only, CSRF | `{"full_name"?,"phone"?}`, at least one. `200` with the same body as the read. |
| `POST /api/v1/me/email-change` | signed in (Individual or Agency, not Admin), CSRF | `{"new_email","current_password"}`. Always `202 {"status":"check_your_email"}`. |
| `POST /api/v1/email-changes/confirm` | public | `{"token"}`. `200 {"status":"email_changed"}` (`no-store`). |

Any other account type on the profile routes is `403 forbidden` (the Agency has its own sibling route, below); an
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

# Agency business profile (Story 1.21, backend half)

A sibling of the Individual profile, built the same way: the account type is checked first (an Individual or
Administrator gets `403 forbidden` before any body check), the account row and then the profile row are locked for an
update, `account.profile_updated` (no metadata) is audited only when a stored value really changed, bodies are strict and
all failing fields are reported at once with stable codes (values never echoed). Every answer carries `Cache-Control:
no-store`. A Suspended agency may read and update; a Deactivated one has no session.

| Route | Who | Purpose |
| --- | --- | --- |
| `GET /api/v1/me/agency-profile` | signed in, Agency only | the eight fields below (`no-store`). |
| `PATCH /api/v1/me/agency-profile` | signed in, Agency only, CSRF | any of `agency_name`, `description`, `city_id`, `business_phone`, `office_address`, at least one. `200` with the same body as the read. |

Read body: `agency_name`, `description`, `city_id`, `business_phone`, `office_address`, `email`, `pending_email`,
`verification_status`. `email` and `pending_email` are exactly as for the Individual profile; `verification_status` is
the read-only Agency Verification status (`unverified` until the later verification story). `email` and
`verification_status` are NOT updatable: sending them (or any other field) is `400 validation_error` with
`non_field_errors: unknown_field` and nothing changes. An empty body is a `400 validation_error`.

## Privacy classes

| Field | Class |
| --- | --- |
| `agency_name`, `description`, `city_id` | public (for the later public agency profile) |
| `business_phone` (E.164), `office_address` | private: returned only by this route, to the owning agency |
| `email`, `pending_email` | private |

`GET /api/v1/me` is unchanged (it carries `agency_name` only: no phone, address, city or description). Neither private
value, nor the email, appears in a log, audit record, event or error body, and the profile view dataclass hides them from
`repr`. Canary tests cover logs, audit metadata, events, error bodies and the mail outbox.

## Fields and codes

| Field | Rule | Codes |
| --- | --- | --- |
| `agency_name` | as registration; contact details refused | `required`, `invalid`, `invalid_name` |
| `business_phone` | as registration; mandatory (blank or `null` is `required`) | `required`, `invalid`, `invalid_phone` |
| `office_address` | as registration; mandatory | `required`, `invalid`, `invalid_address` |
| `city_id` | an ACTIVE City reference id; mandatory | `required`, `invalid`, `invalid_city` |
| `description` | optional | `invalid`, `invalid_description`, `contains_contact_details` |

`description`: blank, whitespace-only or `null` clears it to `""` (the only field that can be cleared). Otherwise it is
NFC-normalised with all whitespace, paragraph breaks included, collapsed to single spaces, then 1 to 1000 characters,
no control or other invisible characters (NUL, bell, bidi overrides; ZWNJ/ZWJ inside Urdu text are fine, but not at
either end), at least one letter or digit, and it must pass the shared public-text check `reject_contact_details`:
phone numbers, email addresses or street addresses give `contains_contact_details` ("Descriptions cannot contain phone
numbers, email addresses or street addresses."). A wrong shape gives `invalid_description`; a non-string gives `invalid`.
The agency name keeps `invalid_name`, whose message already says no phone numbers or email addresses.

The city is looked up (through `catalog.selectors`, never the catalog models) only when `city_id` was sent, differs from
the stored city and every other field passed. The lookup runs inside the transaction under the row locks, which narrows
but does not eliminate a race with a concurrent deactivation (nothing locks the City row), so a city deactivated at that
exact moment can still be saved. Resending the stored city is never looked up: an agency whose city was deactivated can
still edit its name, description, phone and address. A changed city that is unknown or inactive is `invalid_city`.
Non-string descriptions are `invalid_description`; a combining mark must follow a letter or another mark, as for the
agency name. The account type is checked before the body in the service too (an Individual gets `forbidden` even with
an empty or invalid body).

## Known gaps in the contact-detail check

The check on `description` (and the agency name) uses the shared patterns of Story 1.14, which are best-effort. Known
gaps: phone numbers split by words or commas, very long separator runs, "info at agency.com", Urdu digit words, and
"Plot/Block/Sector/Phase" addresses (deliberately allowed as public location). Public text can therefore still carry
contact details that evade the heuristics; hardening the shared patterns is a separate story (see
`docs/privacy-patterns.md`).

## Email change

The routes of Story 1.20 (`POST /api/v1/me/email-change`, `POST /api/v1/email-changes/confirm`) already work for Agency
accounts and are unchanged. While a request is pending `pending_email` shows in the agency profile, computed exactly as
for the Individual profile.

## Frontend hand-off

Operations `get_agency_profile` and `update_agency_profile`. Show `fields.<name>` per input; `description` may be cleared
by sending an empty string; send only the changed fields; show the verification status read-only; show `pending_email` as
"waiting for confirmation of ...". The email is changed through the existing change-email form, not here.
