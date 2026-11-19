---
title: 'Story 1.12: Account model, states and capability matrix'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '171e21940cda1931a15164c4d785c6851235cd6a'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/planning-artifacts/prds/prd-estate-ai-backend-2026-11-18/prd.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-11-city-reference-data.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** There is no account model, so restrictions (unverified email, suspension, deactivation) cannot yet be applied consistently, and later stories have nothing to lock, check or audit.

**Approach:** Make `identity` the second installed domain app. It owns an `Account` table whose state is derived from three separate conditions (email verified, suspended, deactivated), a complete FR-74 capability matrix with `identity.can(actor, capability)` where capabilities that depend on later stories are marked pending, state-changing services that audit every change, `lock_account` and `is_active_owner` as the only lock and eligibility primitives, and the `account.session_ended` event. No registration, sign-in or session code is added here.

## Boundaries & Constraints

**Always:** `apps.identity` is added to `INSTALLED_APPS` with an `AppConfig`; `Account` has a UUID primary key, `email` (trimmed, as entered) with a case-insensitive unique constraint on `Lower("email")`, a `password` field holding only a Django password hash (hashing through `django.contrib.auth.hashers`; the Django auth, sessions and admin apps stay uninstalled), `account_type` (`individual`, `agency` or `admin`), `email_verified_at`, `suspended_at`, `suspension_reason` (blank unless suspended, 1 to 300 characters when set), `deactivated_at`, `created_at` and `updated_at`, with database check constraints that a suspension reason exists exactly when `suspended_at` is set; the account state is derived, never stored: deactivated beats suspended beats email-unverified beats active, in the four values `email_unverified`, `active`, `suspended`, `deactivated`; the FR-74 matrix is a complete table over the ten capabilities `sign_in`, `browse`, `view_drafts`, `edit_drafts`, `publish`, `send_messages`, `read_conversations`, `report`, `request_support`, `request_deactivation` and the four states exactly as in PRD FR-74 (suspended: can sign in, browse, view drafts and read conversations, cannot edit drafts, publish, send messages or report, can request support and deactivation; deactivated: browse at Visitor level only, nothing else, no sign-in); a pure function returns each cell and `can(actor, capability)` returns true only when the cell is allowed and the capability is not pending; Visitor actors get `browse` only, system actors are allowed everything, admin actors get `sign_in` and `browse` only (administrators cannot own listings or message), user actors are evaluated from their account's state with one database read, and an unknown or missing account is denied; pending capabilities are everything except `sign_in` and `browse` and live in one `PENDING_CAPABILITIES` set that later stories shrink in their own change together with a test; services take `actor` first: `create_account(actor, email, password, account_type)` (system actor only; basic email shape check; hashed password; duplicate email raises a `DomainError("validation_error")` without disclosing which; writes `account.created`), `mark_email_verified(actor, account_id)` (system actor; idempotent; writes `account.email_verified`), `suspend(actor, account_id, reason)` and `reactivate(actor, account_id)` (administrator actors; reason 1 to 300 characters, no `@` and no digit run of seven or more, reusing the audit module's digit-run constant; writes `account.suspended` and `account.reactivated`; reactivation clears suspension only, so the state returns to active if email is verified, else email-unverified), `deactivate(actor, account_id)` (administrator actor; writes `account.deactivated`, emits `account.session_ended` with reason `deactivated`; a deactivated account cannot be reactivated); every state change and its audit record are written in the same transaction (audit actions are registered in `identity`'s `AppConfig.ready` with metadata schemas that hold only enumerated values such as the account type, never email, phone or free text; the suspension reason lives on the account row only, never in audit metadata); non-administrator callers get `unauthenticated` for visitors and `not_found` for others, and an unknown account id is `not_found`; `lock_account(account_id)` locks the row with `select_for_update` inside an open transaction and returns an immutable snapshot (id, type, state), and `is_active_owner(account_id)` is true only for an active individual or agency account; the `AccountSessionEnded` event (name `account.session_ended`, fields account id and a reason from `sign_out`, `password_changed`, `deactivated`) is defined in `identity/events.py` and a helper emits it, so sign-out and password-change stories can reuse it; `identity.selectors` returns plain immutable values and never model instances (account view with id, type, state and the suspension reason, no email or password); a deactivated or unknown account cannot be returned as signed in by any selector; test settings use a fast password hasher; the runtime role gets DML through the existing hook; import-linter contracts, convention tests, `openapi.yaml` and the authorisation matrix keep passing; comments only where intent is not obvious.

**Never:** build registration, verification links, sign-in, sessions, password reset or change, profiles, phone numbers or addresses, Agency fields, administrator provisioning, endpoints, notifications or email; install Django's auth, sessions or admin apps; store the email or reason in audit metadata or logs; activate any pending capability; archive Listings (no Listings exist yet).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| State derivation | combinations of the three conditions | deactivated, then suspended, then email-unverified, else active | N/A |
| Matrix | every state by every capability | each cell equals PRD FR-74 (table-driven test) | N/A |
| Pending | `can` on a pending capability for an allowed state | false until the story activates it; the table cell still reads as FR-74 | N/A |
| Visitor | Visitor actor | browse only | N/A |
| Admin actor | administrator account | sign in and browse only | N/A |
| System actor | system actor | allowed | N/A |
| Missing account | user actor whose account is gone | denied | N/A |
| Create | system actor creates an account | row with hashed password, no verification, audit `account.created` | N/A |
| Duplicate email | same email in another case | refused, nothing written | generic validation error |
| Verify | `mark_email_verified` twice | verified once, second call no-op, one audit record | N/A |
| Suspend | administrator with a valid reason | state suspended, reason stored, audit written, no event | N/A |
| Bad reason | blank, over 300, `@` or seven digits | refused, nothing written | validation error |
| Reactivate verified | suspended and verified | active | N/A |
| Reactivate unverified | suspended and not verified | email-unverified | N/A |
| Deactivate | administrator | state deactivated, audit written, `account.session_ended` after commit | N/A |
| Deactivated sign-in | `can(user, sign_in)` | denied | N/A |
| Reactivate deactivated | any actor | refused | `not_found`-style error |
| Non-admin caller | visitor or user on a service | `unauthenticated` or `not_found`, nothing written | N/A |
| Rolled back | failure after the audit write | account and audit unchanged | N/A |
| Locking | `lock_account` outside a transaction | refused | `RuntimeError` |
| Locking | two transactions lock one account | second waits until the first ends (threaded test) | N/A |
| Eligibility | `is_active_owner` | true only for active individual or agency | N/A |
| Privacy | audit metadata and logs after any change | no email, no password hash, no reason text | canary test |
| Runtime role | `runtime` role | DML on the account table, no DDL | role test |

</frozen-after-approval>

## Code Map

- `apps/identity/apps.py`, `models.py`, `migrations/0001_initial.py` -- app config (registers audit actions in `ready`), `Account` with constraints; keep `events.py` for the event class
- `apps/identity/state.py` (pure: `AccountState`, `derive_state`), `capabilities.py` (pure: `Capability`, `Access`, `MATRIX`, `matrix_access`, `PENDING_CAPABILITIES`), `can.py` or `policy.py` (`can(actor, capability)`)
- `apps/identity/services.py`, `selectors.py`, `events.py` -- services listed above, `lock_account`, `is_active_owner`, selectors, `AccountSessionEnded`
- `config/settings/base.py` -- add `apps.identity`; `config/settings/test.py` -- fast hasher
- `tests/identity/` -- table-driven matrix, state, services, audit, event, locking (threaded), privacy canary, selectors, grants; `tests/test_db_roles.py` -- runtime role on the table
- `docs/account-states.md`, `README.md` -- states, capability matrix, pending cells and how a later story activates one, the services and primitives

## Tasks & Acceptance

**Execution:**
- [x] `apps/identity` app config, model, migration, constraints -- Account table
- [x] `state.py`, `capabilities.py`, `can` -- derived state, FR-74 matrix, `can`
- [x] `services.py`, `selectors.py`, `events.py` -- audited state changes, lock and eligibility primitives, session-ended event
- [ ] settings, tests, role test -- cover every matrix row
- [ ] docs and README

**Acceptance Criteria:**
- Given an account, when its state is evaluated, then email verification, suspension and deactivation are separate conditions yielding Email-unverified, Active, Suspended or Deactivated, and `identity.can` implements the FR-74 matrix.
- Given the matrix, when the table-driven test runs over states and capabilities, then each cell matches FR-74, with capabilities that depend on later FRs marked pending.
- Given a Deactivated account, when it tries to sign in, then `can(sign_in)` refuses; given a Suspended account, then it may sign in and restricted actions are refused.
- Given a Suspended account being reactivated, when its email is verified, then it returns to Active, otherwise to Email-unverified; `lock_account` and `is_active_owner` exist as the only lock and eligibility primitives.
- Given the Account model, when created, then it holds a case-insensitively unique email, a password hash and the three conditions, every state change writes an audit record, and identity emits `account.session_ended` for deactivation (and defines it for sign-out and password change).

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | Admin-type account acting as a user would gain publish and message once activated; ADMIN actor never reads its account (BH, EC, VG) | high | patch | Contradicted "administrators own no listings"; admin capability set applied by account type, deactivated or missing admins denied. |
| 2 | Administrator can suspend or deactivate themselves or another administrator, irreversibly (BH, EC) | high | patch | Services refuse self and admin targets. |
| 3 | Password and email input gaps: blank or unbounded passwords, control characters, empty domain labels (BH, EC) | medium | patch | Rejected with tests. |
| 4 | Any IntegrityError reported as duplicate email (EC) | medium | patch | Only the email constraint is converted. |
| 5 | Suspension reason with separated or Unicode digits passes the digit-run check (EC) | medium | patch | Total digit count checked. |
| 6 | Non-canonical UUID forms accepted by `parse_id` (BH) | low | patch | Canonical form only. |
| 7 | Failure after the session-ended event untested; hygiene issues; bare assert (BH, VG) | low | patch | Fixed. |
| 8 | Docs: suspension reason cleared on reactivation, `can` entry point, lock snapshot with deactivated state (BH, EC) | low | patch | Documented. |
| 9 | Distinct error codes for conflicts, enumeration handling (BH) | low | reject | Registration (Story 1.15) owns enumeration-safe behaviour. |
| 10 | Python vs Postgres lowercasing; non-default DB alias (EC, BH) | low | reject | Race path still caught by the constraint. |
| 11 | Runtime role has DELETE on accounts (BH) | low | reject | Blanket DML by design across tables. |
| 12 | Suspension does not end sessions (EC) | low | reject | FR-74: suspension keeps sessions valid; restricted actions refused per request. |
| 13 | `identity.can` not exported from the package (EC) | low | reject | Models cannot be imported at app load; documented entry point is `apps.identity.policy.can`. |
| 14 | Deactivated administrator denied browse, unlike ordinary deactivated accounts (implementer note) | low | reject | Administrators cannot be deactivated through the services, so the case is unreachable. |

## Design Notes

Decisions made without asking: ten capabilities because FR-74 splits Reports from support and deactivation requests for Suspended accounts and treats viewing Drafts differently from editing them; the table is complete and pure while `can` additionally hides pending capabilities so a later story activates a cell by removing it from one set, with its own test; the account is the only identity table here (profiles, phone and Agency data belong to Stories 1.15 to 1.21); `create_account` and `mark_email_verified` are system-actor services used by registration and verification; the suspension reason is stored on the row, not in audit metadata, so the append-only audit table never holds free text; reactivation of Deactivated accounts is unsupported per PRD; passwords use Django's hashers without installing the auth app so Story 1.17 can build sessions without a competing user model; session termination itself arrives with Story 1.17 and listens to the event.

## Verification

**Commands:**
- `uv run python manage.py migrate --database=migrator` -- applies the identity migration
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest && uv run python manage.py makemigrations --check --dry-run` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && git diff --exit-code openapi.yaml` -- expected: no diff
