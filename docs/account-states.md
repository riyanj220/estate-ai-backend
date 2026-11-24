# Account model, states and capabilities

`identity` owns the `identity_account` table. Nothing outside `identity` imports its models (import-linter enforces it).
Django's auth, sessions and admin apps stay uninstalled; passwords are stored as Django password hashes made with
`django.contrib.auth.hashers` (test settings use a fast hasher).

## The account

UUID `id`, `email` (trimmed, as entered; unique ignoring case through a `Lower("email")` constraint), `password` (hash only),
`account_type` (`individual`, `agency`, `admin`), `email_verified_at`, `suspended_at`, `suspension_reason`,
`deactivated_at`, `created_at`, `updated_at`. A check constraint ties the reason to the suspension: it is set exactly when
`suspended_at` is set (1 to 300 characters).

## States are derived, never stored

Three separate conditions give one of four states; the first match wins:

| Condition | State |
|---|---|
| `deactivated_at` set | `deactivated` |
| `suspended_at` set | `suspended` |
| `email_verified_at` empty | `email_unverified` |
| otherwise | `active` |

`identity.state.derive_state` is the pure function. Reactivating a suspended account clears only the suspension, so it
returns to `active` if the email is verified, otherwise to `email_unverified`. A deactivated account is never reactivated. The suspension reason is deliberately cleared on reactivation; only the
audit record of the reactivation remains (moderation history lives in audit and, later, Reports).

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

Every capability except `sign_in`, `browse`, `view_drafts` and `edit_drafts` depends on a later story and is in
`PENDING_CAPABILITIES` (`identity/capabilities.py`), so `can` answers false for it today while the table still reads
as FR-74. Story 2.1 activated `view_drafts` and `edit_drafts` (Draft creation and reads, `docs/listings.md`): `can`
now follows the table for them, so a session's `capabilities` list includes them (an Email-unverified or Active
account gets both, a Suspended one `view_drafts` only, a Deactivated one neither, administrators neither).
A later story activates a capability by removing it from that one set, in its own change, together with a test that
`can` now follows the table for it (see `test_activated_capability_follows_the_table`, which patches the set, and
`test_draft_capabilities_are_live_and_follow_fr74`).

## Services (system or administrator actor first, all audited in the same transaction)

| Service | Caller | Audit action | Notes |
|---|---|---|---|
| `create_account(actor, email, password, account_type)` | system | `account.created` | duplicate email (any case) is a generic `validation_error` |
| `mark_email_verified(actor, account_id)` | system | `account.email_verified` | idempotent; a second call writes nothing |
| `suspend(actor, account_id, reason)` | administrator | `account.suspended` | reason 1 to 300 characters, no `@`, no run of seven or more digits; already suspended is `validation_error` |
| `reactivate(actor, account_id)` | administrator | `account.reactivated` | not suspended is `validation_error` |
| `deactivate(actor, account_id)` | administrator | `account.deactivated` | emits `account.session_ended` (reason `deactivated`) after commit |

`suspend`, `reactivate` and `deactivate` refuse (`validation_error`, nothing written) when the target is the actor's own
account or any `admin` account; administrator accounts are managed only by the `provision_admin` platform command (Story 1.22, `docs/administrators.md`). Visitors get
`unauthenticated`, other callers `not_found`; an unknown account id, and any change to a deactivated
account, is `not_found`. Audit metadata holds only the account type on creation. Email, password hash and the suspension
reason (kept on the row; digit characters of any script are counted, seven or more are refused) never reach audit metadata or logs.

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
