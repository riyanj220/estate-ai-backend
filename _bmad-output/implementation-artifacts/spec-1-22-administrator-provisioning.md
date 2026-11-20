---
title: 'Story 1.22: Administrator provisioning and protected admin API'
type: 'feature'
created: '2026-11-20'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-17-sign-in-and-sign-out.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-18-password-reset-and-change.md'
  - '{project-root}/docs/sessions.md'
  - '{project-root}/docs/authorisation-matrix.md'
  - '{project-root}/docs/account-states.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Administrator accounts have no safe way to come into existence, sign in, or be protected: there is no provisioning path, no admin sign-in, no admin-only API base, and administrators currently share the ordinary session lifetimes and password rules.

**Approach:** Provision administrators only through a platform management command (create, disable, remove, reset password), each audited and with no default credentials; give administrators their own sign-in endpoint with its own failure throttle, audit records, strong password policy and short sessions (30 minutes idle, 8 hours absolute); add the protected `/api/v1/admin/*` base with a route-enumeration test proving every admin route requires the Administrator capability and no public path can create an Administrator; and keep Administrators from owning Listings. Backend only (the admin shell is Story 1.23). Strengthened second-factor authentication (FR-10) is out of scope.

## Boundaries & Constraints

**Always:**
- **Provisioning command** `python manage.py provision_admin <create|disable|remove|reset-password> --email <address>`. The password for `create` and `reset-password` is read interactively twice with `getpass` (no echo), or from standard input with `--password-stdin` for automation; it is never a command-line argument, never defaulted, never printed, never logged, never written to a file. No settings key, fixture, migration or seed contains an administrator or a default credential. The command calls `identity.services` functions as the system actor (actor-first, `_require_system`), never touches models directly, and exits non-zero with a one-line message (no stack trace, no password) on any refusal.
  - `create`: validates the email (`check_email`) and the strong administrator password policy, refuses an address that already has any account, creates an Account of type `admin`, email already verified, no profile row, state Active; audit `admin.provisioned` (target = the account id, empty metadata).
  - `disable`: sets the account Deactivated (cannot sign in) and ends every open session (reason `deactivated`); idempotent on an already disabled administrator; refuses non-administrators ("not an administrator"); audit `admin.disabled`.
  - `remove`: deletes the administrator account (cascades its sessions and tokens); refuses non-administrators; refuses to remove the LAST active administrator unless `--force-last` is given; audit `admin.removed` with the former account id as target (the audit table keeps no foreign key to accounts).
  - `reset-password`: sets a new password after the strong policy check, ends every open session (reason `password_changed`), voids outstanding tokens; refuses non-administrators; audit `admin.password_reset`. This is the ONLY way a forgotten administrator password is recovered: the public password reset request and confirm treat administrator accounts as unknown (nothing sent, `link_invalid`).
  - All four actions are recorded with new audit actions registered from `AppConfig.ready` (empty metadata schemas; never an email, password or hash in metadata). Unknown email, refusals and policy failures write no audit record.
- **Strong administrator password policy** (new pure function beside `password_error`): at least 12 characters, at most 1024, contains at least one letter and one digit, not a common password, not the email address or its local part, no leading or trailing whitespace. Codes: `required`, `invalid`, `password_too_long`, `password_matches_email`, `password_too_common`, plus new `admin_password_too_short` and `admin_password_weak` (letters and digits) with static catalogue messages. It applies at `create`, at `reset-password` and when an administrator changes their own password through the existing `POST /api/v1/me/password` (the change service picks the policy by account type; the same-as-current check stays).
- **Admin sign-in** `POST /api/v1/admin-session` (not under `/api/v1/admin/`), body strict `{"email","password"}`, public, CSRF enforced like the ordinary sign-in, `authentication_classes = []`. Only accounts of type `admin` that are not Deactivated can sign in here; every other outcome (wrong password, unknown email, Individual or Agency credentials, a disabled administrator, a throttled attempt) is the identical generic `401 invalid_credentials` with identical headers and body, and a non-administrator's password is still hashed against the dummy hash so timing does not distinguish them (same dummy-hash and advisory-lock pattern as Story 1.17). Failed attempts are throttled by a new seeded policy `admin_sign_in` (5 per 900 s, key by lowercased email and client network, counted on failures only, serialised per key, silent generic 401 when the cap is reached, never a 429). Success starts a session exactly like Story 1.17 (new token, hash only, HttpOnly SameSite=Lax cookie, fixation defence, CSRF rotated, per-account open-session cap) and returns the actor summary. Audit records: `admin.signed_in` for a success (target the account) and `admin.sign_in_failed` for a failed attempt against an EXISTING active administrator account (target the account; no record for unknown emails or non-administrators, so the audit trail cannot be used to probe addresses and cannot be flooded by strangers); neither carries metadata. The ordinary `POST /api/v1/session` refuses administrator accounts with the same generic `invalid_credentials`, so an administrator session can only be created here.
- **Administrator session lifetimes**: sessions of administrator accounts last at most `ADMIN_SESSION_ABSOLUTE_SECONDS` = 8 hours and expire after `ADMIN_SESSION_IDLE_SECONDS` = 30 minutes without use (settings with those defaults, validated like the other session settings: positive, touch < idle <= absolute). The absolute limit is stored on the session row at creation; the idle limit follows the account type at resolution time (`live_session` and the touch rule) so an administrator session cannot outlive 30 idle minutes even if the ordinary idle limit is longer; the touch interval stays 5 minutes. Sign-out and password change use the existing routes (`DELETE /api/v1/session`, `POST /api/v1/me/password`); administrators cannot request an email change (already `forbidden`).
- **Protected admin API base**: a new `apps/presentation/adminapi` package already exists; add its first route `GET /api/v1/admin/me` (`RequireAdmin`): `200 {"id","account_type":"admin","state"}` and nothing private, `Cache-Control: no-store`. Visitors get 401 `unauthenticated`, signed-in non-administrators get 404 `not_found` (the admin area is not confirmed to them), exactly as the existing `require_admin` rule and `docs/authorisation-matrix.md` state. Route enumeration test: every URL pattern whose path starts with `api/v1/admin/` resolves to a view whose permission classes are exactly `RequireAdmin` and that sets `authentication_classes` explicitly; a second test enumerates every registration route (`api/v1/registrations/*`) and every service reachable from them and proves none can create an account of type `admin` (a hostile `account_type` or `role` body is refused as `unknown_field`, and the registration services only create `individual` and `agency`); a third proves `identity.services.create_account` refuses type `admin` for any non-system actor.
- **Administrators cannot own Listings**: `identity.selectors.is_active_owner` and the capability matrix already exclude administrators; add tests proving an administrator is not an eligible owner and that the Listing-creation capability is not granted to an administrator account in any state (the Listing feature itself arrives later, so this is a policy test only).
- **Operator audit trail**: the existing audit read path (Story 1.10) is unchanged; add tests that each provisioning action and each admin sign-in outcome writes exactly one audit record with the right action and target, and none of them contains an email, password, hash, token or session value (canary test over logs, audit metadata and errors, including the command's output).
- **Docs**: new `docs/administrators.md` (provisioning command usage, the recovery rule, the strong policy, the admin sign-in, lifetimes, the audit actions, the route enumeration rule and how Story 1.23 should use `/api/v1/admin/me` and `/api/v1/admin-session`), updates to `docs/sessions.md`, `docs/account-states.md`, `docs/authorisation-matrix.md` (regenerated), `docs/password-reset.md` (administrators are excluded), `docs/rate-limits.md`, `README.md`; `openapi.yaml` regenerated. All earlier tests, convention tests and import-linter contracts keep passing. Comments only where intent is not obvious.

**Never:** create an Administrator through any HTTP route, registration path, migration, fixture, seed or setting; have a default or example credential anywhere; accept the password as a command-line argument or print it; let a non-administrator's credentials succeed at the admin sign-in or an administrator's at the ordinary sign-in; reveal through status, body, headers or timing whether an address is an administrator; return 429 from the admin sign-in throttle; record audit entries for unknown emails; let administrator sessions use the ordinary lifetimes; let the public password reset recover an administrator; return the administrator's email or any private data from `/api/v1/admin/me`; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Create admin | `create --email a@x --password-stdin` with a strong password | account type admin, verified, Active, no profile; audit `admin.provisioned`; exit 0 | N/A |
| Create, weak password | under 12 characters, no digit, common, equals email | refused with the policy message, no account, no audit; non-zero exit | message only |
| Create, existing address | any account with that address | refused, nothing changes; non-zero exit | message only |
| No default credentials | repository, migrations, settings, fixtures | no administrator exists after `migrate`; enumeration test | test |
| Password via argv | `--password` flag | not accepted (no such option) | argparse error |
| Disable | existing administrator | Deactivated, sessions ended, audit `admin.disabled`; sign-in refused afterwards | N/A |
| Disable non-admin or unknown | | refused, nothing changes, no audit | message only |
| Remove | administrator | account deleted, audit `admin.removed`; sessions gone | N/A |
| Remove last active admin | only one active administrator | refused without `--force-last` | message only |
| Reset password | administrator | new password works, old fails, all sessions ended, audit `admin.password_reset` | N/A |
| Public reset for an admin | request and confirm | nothing sent, `link_invalid`; identical 202 on request | test |
| Admin sign-in ok | administrator, right password, CSRF | 200 summary, cookie, audit `admin.signed_in` | N/A |
| Non-admin at admin sign-in | Individual or Agency credentials | identical generic 401, no session, no audit | envelope |
| Admin at ordinary sign-in | administrator credentials | identical generic 401, no session | envelope |
| Wrong password, unknown email, disabled admin | | identical generic 401; failure audited only for an existing active administrator | envelope |
| Timing | admin vs non-admin vs unknown | one password hash comparison on each path | test counts hasher calls |
| Throttle | 5 failures then the correct password | generic 401 throughout, clears after the window, no 429 | test with clock |
| No CSRF | admin sign-in without a token | 403 `forbidden` | envelope |
| Idle expiry | admin session idle 31 minutes | 401; an ordinary session of the same age stays valid | test with clock |
| Absolute expiry | 8 hours and 1 minute with activity | 401 | test with clock |
| Sliding refresh | request within 5 minutes of last use | `last_seen_at` unchanged | N/A |
| Admin sign-out | valid admin session + CSRF | 204, session ended | N/A |
| Admin password change | right current password | strong policy applies; weaker-than-policy new password refused with the admin codes; other sessions end | N/A |
| Admin email change | request | 403 `forbidden` | envelope |
| Admin route access | Visitor / Individual / Agency / admin on `GET /api/v1/admin/me` | 401 / 404 / 404 / 200 | envelope |
| Route enumeration | every `api/v1/admin/*` pattern | permission classes exactly `RequireAdmin` | test |
| Registration enumeration | every registration route and service | cannot create an admin; hostile `account_type` refused | test |
| Create account service | non-system actor, type admin | refused | test |
| Listing ownership | administrator in any state | not an eligible owner; no Listing-creation capability | test |
| Privacy | logs, audit metadata, errors, command output | no email, password, hash, token (canary) | test |
| Contract | `openapi.yaml`, authorisation matrix | new routes, codes; freshness passes | test |

</frozen-after-approval>

## Code Map

- `apps/kernel/management/commands/provision_admin.py`? No: management commands live per app: `apps/identity/management/commands/provision_admin.py` (check how `run_retention` is placed and follow the layering contracts; if a command in identity violates a contract, place it where `run_retention` lives and call identity services through the allowed direction)
- `apps/identity/services.py` (`provision_admin`, `disable_admin`, `remove_admin`, `reset_admin_password`, `admin_sign_in`, strong policy in change), `passwords.py` (strong policy), `sessions.py` (admin lifetimes), `selectors.py` (`live_session` idle by type), `apps.py` (audit actions), `validation.py`
- `apps/kernel/errors.py` (messages), `apps/kernel/migrations/0009_seed_admin_sign_in_policy.py`
- `apps/presentation/api/sessions.py` (admin sign-in view; ordinary sign-in refuses admins), `apps/presentation/adminapi/` (`me` view), `config/urls.py`, `config/settings/base.py` (admin lifetimes)
- `tests/identity/test_admin.py`, `tests/api/test_admin_api.py`, `tests/authz/` route enumeration, command tests
- `docs/administrators.md` and updates; `openapi.yaml`, `docs/authorisation-matrix.md` regenerated

## Tasks & Acceptance

**Execution:**
- [ ] strong policy, provisioning services, audit actions
- [ ] management command with stdin and prompt password handling
- [ ] admin sign-in, ordinary sign-in refusal, throttle seed, admin lifetimes
- [ ] admin API base and route enumeration tests
- [ ] tests covering every matrix row; docs

**Acceptance Criteria:**
- Given the platform command, when I provision, disable or remove an Administrator, then each is recorded in the audit trail, no default credentials exist, and a test enumerating all routes finds no registration path that creates an Administrator.
- Given a non-administrator or a Visitor, when it calls any `/api/v1/admin/*` route, then access is denied by the backend and the route test confirms every admin route requires the Administrator capability.
- Given the admin sign-in endpoint, when a non-administrator uses it, then it is rejected, and failed and successful administrator sign-ins are throttled where failed and recorded in the audit trail, and administrator passwords follow the strong policy.
- Given an administrator session, when it is idle for 30 minutes or 8 hours have passed, then it expires; the administrator can sign out and change their password while signed in, and a forgotten password is reset only through the provisioning command.
- Given an Administrator account, when it tries to create a Listing, then it is refused because Administrators cannot own Listings.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (blind, edge-case, verification-gap).

| # | Finding | Verdict | Route |
|---|---------|---------|-------|
| 1 | Admin failure throttle was per email and network only, so a distributed attacker got 5 guesses per address against one administrator (blind) | high | patch: second silent per-account counter (`admin_sign_in_account`, 20 per hour); either cap gives the generic 401 |
| 2 | `confirm_email_change` did not refuse administrator accounts, so a stray link-issued row could swap an administrator's address (edge) | high | patch: `link_invalid` for administrators, route tests |
| 3 | Disable had no last-administrator guard and no shared lock, so concurrent disable and remove could leave zero active administrators; no way back from a disabled account (blind, edge) | high | patch: same ordered lock and `--force-last` guard on disable; new `enable` action |
| 4 | Throttled attempts skipped the hash (speed oracle); the failure audit write could roll back the throttle count or turn the 401 into a 500, and could flood (blind, edge) | medium | patch: dummy hash on the throttled branch, audit written after the count commits, skipped past the per-account cap |
| 5 | `--force-last` not recorded; admin lifetimes not bounded by the ordinary ones; purge cutoff ignored account type; two sources for the absolute expiry; string `"admin"` comparisons (blind, edge) | medium | patch |
| 6 | Strong policy accepted non-ASCII letters and digits; whitespace gave a misleading message; stdin handling (empty, CR, over-long, TTY) and unhandled domain errors in the command (edge, blind) | low | patch |
| 7 | Tests: success consuming the budget, HTTP-level policy messages, token cascade on remove, verification tokens on reset, suspended `/admin/me`, no-op assertion (verification-gap) | medium | patch: about 40 tests |
| 8 | Admin and ordinary sessions share the `estate_session` cookie (blind) | medium | defer, documented: a sign-in on one ends the other; separate cookie name or `SameSite=Strict` is a follow-up |
| 9 | Audit has no operator identity, refusals, throttled attempts or sign-outs (blind) | low | defer, documented: accountability relies on OS and SSH logs |
| 10 | `_attempt` takes many arguments; source-text tests are not behavioural; no subprocess test of `manage.py` (blind, verification-gap) | low | defer |

Deviation recorded: the throttled-branch dummy hash also applies to the ordinary sign-in door (one existing test changed).

## Design Notes

Decisions made without asking: administrators use their own sign-in route and the ordinary sign-in refuses them, so administrator sessions always carry the short lifetimes and the admin throttle; failed admin sign-ins are audited only for an existing administrator so the audit trail neither probes addresses nor floods; the public password reset treats administrators as unknown, making the provisioning command the only recovery path as required; removing the last active administrator needs an explicit flag so an operator cannot lock the platform out by accident; the command reads passwords from a prompt or stdin because command-line arguments leak into shell history and process lists; the admin API base ships one read route (`/api/v1/admin/me`) so the shell of Story 1.23 and the route-enumeration test have something real to protect.

## Verification

**Commands (backend):**
- `uv run python manage.py migrate --database=migrator`
- `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run pytest -q && uv run python manage.py makemigrations --check --dry-run`
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- regenerate and commit
