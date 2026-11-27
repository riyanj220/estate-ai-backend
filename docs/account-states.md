# Account model, states and capabilities

`identity` owns the `identity_account` table. Nothing outside `identity` imports its models (import-linter enforces it).
Django's auth, sessions and admin apps stay uninstalled; passwords are stored as Django password hashes made with
`django.contrib.auth.hashers` (test settings use a fast hasher).

## The account

UUID `id`, `email` (trimmed, as entered; unique ignoring case through a `Lower("email")` constraint), `password` (hash only),
`account_type` (`individual`, `agency`, `admin`), `email_verified_at`, `suspended_at`, `suspension_reason`,
`reactivation_reason`, `deactivated_at`, `created_at`, `updated_at`. A check constraint ties the reason to the suspension: it
is set exactly when `suspended_at` is set (at most 500 characters).

## States are derived, never stored

Three separate conditions give one of four states; the first match wins:

| Condition | State |
|---|---|
| `deactivated_at` set | `deactivated` |
| `suspended_at` set | `suspended` |
| `email_verified_at` empty | `email_unverified` |
| otherwise | `active` |

`identity.state.derive_state` is the pure function. Reactivating a suspended account clears only the suspension, so it
returns to `active` if the email is verified, otherwise to `email_unverified`. A deactivated account is never reactivated. The suspension reason is deliberately cleared on reactivation; the reactivation
reason stays on the row (`reactivation_reason`, cleared by the next suspension, in no response). Moderation history lives
in audit and, later, Reports.

## Suspension (Story 2.21)

Suspension changes the account row only. No Listing status changes and no session ends: the state is read on every request,
so the matrix restricts an open session at once and the person can still sign in, read and archive.

- Cannot (`403`): publish and republish (`publishing_unavailable`); correct, create, edit or delete a Draft, edit a Listing
  or its images, mark sold or rented (`forbidden`).
- Can: view own Listings and Drafts, archive, and change the profile and its picture or logo (see below).
- Its Listings, Listing images and Agency logo leave public view (`docs/visibility.md`; images may stay cached for up to
  60 seconds) and return on reactivation to `active`. An `email_unverified` result stays private.
- "Cannot send Messages" is already a `no` in the matrix; there is no messaging yet, so the messaging epic must read
  `send_messages` (and add its test).
- The account itself sees why: `suspension_reason` in the sign-in and `GET /api/v1/me` summary, null unless Suspended. No
  public shape or other user's response carries it.
- The account gets an `account.suspended` or `account.reactivated` notification (empty payload).

## Capability matrix (PRD FR-74)

`identity.capabilities.MATRIX` is the complete table (pure Python); `matrix_access(state, capability)` reads one cell.

| Capability | email_unverified | active | suspended | deactivated |
|---|---|---|---|---|
| `sign_in` | yes | yes | yes | no |
| `browse` | yes | yes | yes | yes (Visitor level) |
| `view_drafts` | yes | yes | yes | no |
| `edit_drafts` | yes | yes | no | no |
| `publish` | no | yes | no | no |
| `send_messages` | no | yes | no | no |
| `read_conversations` | yes | yes | yes | no |
| `report` | yes | yes | no | no |
| `request_support` | yes | yes | yes | no |
| `request_deactivation` | yes | yes | yes | no |

### Listing lifecycle by state (Story 2.17)

No new capability: `listings.status.change_status` reads the matrix (`edit_drafts` for sold and rented, `view_drafts` for
archive), so the table above is unchanged.

| Action on an own Listing | email_unverified | active | suspended | deactivated |
|---|---|---|---|---|
| read any status, list, counts, preview | yes | yes | yes | no |
| archive (published, sold, rented) | yes | yes | yes | no |
| mark sold or rented (published) | yes | yes | no (`403`) | no |
| edit a Published or Archived Listing, change its images | yes | yes | no (`403`) | no |

The `report` capability is live since Story 9.1 (`governance.reports.report_listing`).

Republishing an Archived Listing (Story 2.18, `publication.republish`) is the `publish` row: only an Active owner may (`403 email_not_verified` and `403 publishing_unavailable` otherwise), and a restricted Listing cannot be republished (`403`).

The one-time correction of locality and area (Story 2.19, `publication.correct`) follows the edit row: email_unverified and
active owners yes, suspended `403`; the eligibility read answers `available: false` to a suspended owner.

A restricted Listing may only be archived by its owner; a removed one cannot be changed; sold and rented are view only
(`docs/listings.md`, "Lifecycle after publication").

`identity.policy.can(actor, capability)` (import it with `from apps.identity.policy import can`):

- Visitor: `browse` only. System actor: everything.
- Administrator (an ADMIN actor, or a USER actor whose account type is `admin`): `sign_in` and `browse` only, since
  administrators own no listings and send no messages. The account is read: a missing or deactivated administrator is denied
  everything; a suspended one keeps `sign_in` and `browse`. Administrators are never an eligible Listing owner
  (`identity.selectors.is_active_owner`) in any state, and neither `publish` nor `edit_drafts` is ever granted to one.
  Administrator accounts are created, disabled (state Deactivated), removed and password-reset by the provisioning command
  only (`docs/administrators.md`).
- Other user actors: the account's state is read once (one query) and looked up in the matrix. A missing account is denied.
- A capability in `PENDING_CAPABILITIES` is denied for user actors even where the table says yes. An unknown capability
  name raises `ValueError`.

### Pending capabilities

Every capability except `sign_in`, `browse`, `view_drafts`, `edit_drafts`, `send_messages`,
`read_conversations`, `report` and `request_deactivation` (Story 10.1) depends on a later story and is in `PENDING_CAPABILITIES` (`identity/capabilities.py`), so `can`
answers false for it today while the table still reads as FR-74. Story 2.1 activated `view_drafts` and `edit_drafts`
(Draft creation and reads, `docs/listings.md`), Story 7.1 `send_messages` (Active only) and `read_conversations`
(every state but Deactivated): `can` now follows the table for them, so a session's `capabilities` list includes them.
A later story activates a capability by removing it from that one set, in its own change, together with a test that
`can` now follows the table for it (see `test_activated_capability_follows_the_table`, which patches the set, and
`test_draft_capabilities_are_live_and_follow_fr74`).

## Services (system or administrator actor first, all audited in the same transaction)

| Service | Caller | Audit action | Notes |
|---|---|---|---|
| `create_account(actor, email, password, account_type)` | system | `account.created` | duplicate email (any case) is a generic `validation_error` |
| `mark_email_verified(actor, account_id)` | system | `account.email_verified` | idempotent; a second call writes nothing |
| `suspend_account(actor, account_id, reason)` | administrator | `account.suspended` | reason 5 to 500 characters after trimming, one line, no contact details; Suspended or Deactivated is `conflict` |
| `reactivate_account(actor, account_id, reason)` | administrator | `account.reactivated` | same reason rule; not Suspended is `conflict` |
| `deactivate(actor, account_id)` | administrator | `account.deactivated` | emits `account.session_ended` (reason `deactivated`) after commit |

`suspend_account` and `reactivate_account` answer `not_found` for an `admin` account (as for an unknown id); `deactivate`
refuses (`validation_error`) the actor's own account or any `admin` account. Nothing is written. Administrator accounts are managed only by the `provision_admin` platform command (Story 1.22, `docs/administrators.md`). Visitors get
`unauthenticated`, other callers `not_found`; an unknown account id is `not_found`, as is `deactivate` or
verification on a deactivated account. Audit metadata holds only the account type on creation. Email, password hash and the suspension
reasons (kept on the row; digit characters of any script are counted, seven or more are refused) never reach audit metadata or logs.

## Account closure (Stories 10.1 to 10.3)

An Individual or Agency (Suspended included) asks through `governance.closure.request_closure`; an administrator
approves or rejects with a reason. An approval is one transaction: `identity.services.deactivate`, then every
`kernel.erasure` eraser in layer order (governance, messaging, assistant, valuation, listings, identity). An app that
starts holding account data registers an eraser from its `AppConfig.ready`; the eraser writes
`<app>.account_erased`, never deletes quota, moderation or audit history, and deletes files only after commit.
`deactivate` on its own does not erase.

## Primitives and reads (`identity.selectors`)

- `lock_account(account_id)` takes `SELECT ... FOR UPDATE` on the row, raises `RuntimeError` outside an open transaction and
  returns a frozen `AccountSnapshot(id, type, state)`. Use it to serialise work on one account (for example the
  publication quota). An unknown id is `not_found`. The snapshot can carry a `deactivated` state, so callers must check it.
- `is_active_owner(account_id)` is true only for an `active` individual or agency account (the FR-74 eligible owner).
- `get_account(account_id)` returns a frozen `AccountView(id, type, state, suspension_reason)` (no email or password hash);
  `get_account` also returns deactivated accounts, so sign-in decisions use `get_signed_in_account`, which returns `None` for
  unknown and deactivated accounts. `suspension_reason` is for the account's own sign-in display and administrators.
  Ids are accepted only as a UUID object or its canonical lowercase hyphenated string.
- Input rules: passwords must not be blank or whitespace-only and are at most 1024 characters (strength policy belongs to
  registration); emails may not contain control or non-printable characters or empty domain labels.

## Event

`identity.events.AccountSessionEnded` (`account.session_ended`, fields `account_id` and `reason` of `sign_out`,
`password_changed` or `deactivated`) is emitted with `emit_session_ended(account_id, reason)`; it dispatches after commit.
Story 1.17 subscribes (`identity/subscribers.py`) and ends all of the account's open sessions for `password_changed` and
`deactivated`; `sign_out` ends only the current session itself. See `docs/sessions.md`.

Sign-in (`identity.sign_in`): Email-unverified, Active and Suspended accounts can sign in and receive their state and
capabilities in the summary; a Deactivated account cannot (the same generic `invalid_credentials` as a wrong password). A
Deactivated account's sessions are also refused at authentication time.

`identity.register_individual` (Story 1.15) creates the Email-unverified account and emits `account.registered` (field `account_id`) after commit for Story 1.16; see `docs/registration.md`.

## Profile image and logo by state (Story 2.11)

The picture and logo use the profile PATCH's own gate (no new capability; the table above is not changed). The profile
PATCH reads no capability: only a Deactivated account (no session) is refused, so Email-unverified and Suspended accounts
may edit, which the image routes inherit. Visibility differs by route:

| State | Change picture / logo (`PUT`, `DELETE`) | Owner reads own image | Public logo `GET /api/v1/agencies/{agency_id}/logo` |
|---|---|---|---|
| email_unverified | yes | yes | `404` (never active) |
| active | yes | yes | served (`public, max-age=60`) |
| suspended | yes | yes | `404` (the asset is kept; may stay cached up to 60 seconds) |
| deactivated | no (`401`, no session) | no (`404`) | `404`; the asset is removed on deactivation (AD-23) |

Reactivation of a suspended Agency shows its logo again with no further action. An Individual's picture is owner-only in
every state and has no public route. See `docs/media.md`.

Note the difference from Listing images: uploading a Listing image needs `edit_drafts`, which a Suspended account lacks
(`403`), while the profile picture and logo follow the profile PATCH, which a Suspended account may use. A Suspended
Agency can therefore still change its logo, but the public route stays `404` until it is reactivated.
