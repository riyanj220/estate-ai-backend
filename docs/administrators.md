# Administrators (Story 1.22, backend)

Administrator accounts are provisioned only by a platform command, sign in at their own endpoint, use short sessions and a
strong password policy, and reach a protected `/api/v1/admin/*` API base. They cannot own Listings. Strengthened second-factor
authentication (FR-10) is out of scope. The admin shell is Story 1.23.

## Provisioning command

```
python manage.py provision_admin <create|disable|enable|remove|reset-password> --email <address> [--password-stdin] [--force-last]
```

The command (`apps/identity/management/commands/provision_admin.py`) calls `identity.services` as the system actor and never
touches a model. It exits non-zero with a one-line message (no stack trace) on any refusal.

- **Password**: for `create` and `reset-password` it is read twice from a hidden prompt (`getpass`), or as one line of
  standard input with `--password-stdin` for automation (one line of at most 1026 characters, a trailing CR/LF is
  stripped; refused when stdin is a terminal, use the hidden prompt then). There is no `--password` option (argument abbreviation is off, and a
  rejected argument list never echoes the arguments), nothing is defaulted, printed, logged or written to a file.
- **`create`**: validates the email, applies the strong policy, refuses an address that already has any account, creates an
  `admin` account with the email verified, state Active, no profile. Audit `admin.provisioned`.
- **`disable`**: sets the account Deactivated, ends every open session (reason `deactivated`), voids its tokens. Idempotent
  (the second run changes nothing and writes no second record). Refuses to disable the last active administrator unless
  `--force-last` is given. Audit `admin.disabled`.
- **`enable`**: clears the Deactivated state of a disabled administrator; the sessions ended by `disable` stay ended.
  Idempotent. Audit `admin.enabled`.
- **`remove`**: deletes the account (sessions and every token table cascade). Refuses to remove the last active
  administrator unless `--force-last` is given. Audit `admin.removed`, target the former account id (the audit table keeps no
  foreign key to accounts).
- `disable`, `enable` and `remove` all take the same ordered lock over every active administrator before the target row, so
  concurrent calls cannot deadlock or leave nobody active.
- `admin.disabled` and `admin.removed` carry one enumerated metadata field, `forced` (`yes` when the last-administrator
  guard was overridden by `--force-last`, otherwise `no`). The audit records no operator identity: operator accountability
  relies on OS and SSH logs of whoever ran the command.
- **`reset-password`**: sets a new password under the strong policy, ends every open session (reason `password_changed`),
  voids outstanding tokens. Audit `admin.password_reset`.
- `disable`, `enable`, `remove` and `reset-password` refuse a non-administrator or unknown address ("not an administrator"), writing
  nothing. Unknown emails, refusals and policy failures write no audit record. The command never prints the email.

### Recovery rule

`reset-password` is the only way a forgotten administrator password is recovered. The public password reset treats an
administrator as unknown: the request answers the same `202` and sends nothing, and a confirm is `link_invalid`
(`docs/password-reset.md`).

### No default credentials

No setting, fixture, migration or seed contains an administrator. `tests/authz/test_admin_routes.py` checks that no
migration, settings module or fixture refers to administrator creation and that no administrator exists after `migrate`.

## Strong password policy

`identity.passwords.admin_password_error`, applied at `create`, `reset-password` and when an administrator changes their own
password at `POST /api/v1/me/password` (`passwords.policy_error` picks the rule by account type; the "same as current" check
stays). At least 12 and at most 1024 characters, at least one letter and one digit, no leading or trailing whitespace, not a
common password, not the email address or its local part. The letter and the digit must be ASCII (digits of other scripts
do not count). Codes: `required`, `invalid`, `password_too_long`, `admin_password_too_short`,
`admin_password_whitespace`, `admin_password_weak`, `password_too_common`, `password_matches_email`.

## Admin sign-in

`POST /api/v1/admin-session` (not under `/api/v1/admin/`), public, CSRF enforced like the ordinary sign-in,
`authentication_classes = []`, strict body `{"email", "password"}`. `200` returns the actor summary and the HttpOnly session
cookie (new token, hash stored only, fixation defence, CSRF token rotated, the per-account open-session cap).

- Only an `admin` account that is not Deactivated can sign in here. Everything else (wrong password, unknown email,
  Individual or Agency credentials, a disabled administrator, a throttled attempt) is the identical generic
  `401 invalid_credentials`. A non-administrator's password is still hashed against the dummy hash, so there is exactly one
  hash comparison on every path.
- The ordinary `POST /api/v1/session` refuses administrator accounts with the same generic error, so an administrator
  session can only be created here and always carries the short lifetimes and this throttle.
- **Throttle**: policy `admin_sign_in` (5 per 900 s), keyed by lowercased email and client network, failures only,
  serialised per key with the same advisory lock as the ordinary sign-in; the silent generic 401 at the cap, never 429.
  A second silent ceiling, policy `admin_sign_in_account` (20 per 3600 s, keyed by the lowercased email only, any network),
  bounds guessing from many addresses; when either cap is reached the attempt is refused without verifying. A throttled
  attempt still costs one dummy-hash comparison (so it takes as long as a real one), on the ordinary door too. Only failures
  count; successes consume nothing.
- **Audit**: `admin.signed_in` for a success (target the account) and `admin.sign_in_failed` for a failed attempt against an
  EXISTING active administrator (target the account). Unknown emails and non-administrators write nothing, so the trail
  cannot probe addresses or be flooded by strangers; a throttled attempt (no lookup is made) writes nothing either, and
  failures past the per-account ceiling are not audited. The failure record is written after the failure count has
  committed; an audit fault is logged by exception class only and never undoes the count or changes the 401. Neither
  action has metadata. Sign-out is not audited.
- Sign-out and password change use the existing routes (`DELETE /api/v1/session`, `POST /api/v1/me/password`). An
  administrator email change is `403 forbidden`.

## Lifetimes

Idle `ADMIN_SESSION_IDLE_SECONDS` = 30 minutes, absolute `ADMIN_SESSION_ABSOLUTE_SECONDS` = 8 hours. See `docs/sessions.md`.

Settings are validated at load: touch < admin idle <= admin absolute, and neither above its ordinary counterpart. The
absolute limit is the `expires_at` stored at creation (the single source); the idle limit follows the account type when
the session is resolved. Retention (`identity.sessions`) purges administrator rows by the administrator idle limit and
ordinary rows by the ordinary one, so it never deletes a valid session.

Known follow-up (deferred): administrator and ordinary sessions share the `estate_session` cookie, so a sign-in on one door
ends the session held by the other in the same browser. A separate cookie name and `SameSite=Strict` for the admin cookie
are a later hardening step.

## Admin API base

Every route whose path starts `api/v1/admin/` must declare `permission_classes = [RequireAdmin]` and set
`authentication_classes` explicitly on its own view class. `tests/authz/test_admin_routes.py` enumerates the URL
configuration and fails otherwise, and checks a Visitor gets 401 and a signed-in Individual or Agency gets 404 on each.
The same file proves that every registration route refuses a hostile `account_type` or `role` (`unknown_field`), that only
`_insert_account` and `provision_admin` write an account row (registration passes only `individual` or `agency`), that no
presentation module references the provisioning services, and that `create_account` refuses type `admin` for any
non-system actor.

`GET /api/v1/admin/me` returns `{"id", "account_type": "admin", "state"}` and nothing private (`Cache-Control: no-store`).

### For Story 1.23

The shell signs in at `POST /api/v1/admin-session` (fetch `GET /api/v1/csrf` first and send `X-CSRFToken`), then calls
`GET /api/v1/admin/me` to confirm the session: `401` means sign in, `404` means a signed-in non-administrator (show nothing
about an admin area). Expect the session to end after 30 idle minutes or 8 hours and treat any `401` as signed out. Sign out
with `DELETE /api/v1/session`.

## Listings

Administrators own no Listings: `identity.selectors.is_active_owner` is false for them in every state and the capability
matrix never grants `publish` or `edit_drafts` to an administrator.

## Audit actions

`admin.provisioned`, `admin.enabled`, `admin.password_reset`, `admin.signed_in`, `admin.sign_in_failed` (empty metadata) and
`admin.disabled`, `admin.removed` (metadata `forced`: `yes` or `no`), all registered in `IdentityConfig.ready` (never an
email, password or hash).
