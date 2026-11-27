# EstateAI backend

Django 5.2 (Python 3.12) served by a single Daphne process, with PostgreSQL 18 + pgvector.

## Prerequisites

Docker (with Compose), Python 3.12 and [uv](https://docs.astral.sh/uv/).

## Setup

```bash
python3 scripts/bootstrap_env.py        # writes the ignored .env with random secrets
docker compose up -d --wait             # PostgreSQL + pgvector and Mailpit, 127.0.0.1 only
uv sync --frozen
uv run python manage.py migrate --database=migrator
uv run python manage.py register_valuation_model   # the committed model, see docs/valuation.md
scripts/dev_server.sh                   # Daphne on DJANGO_BIND_HOST:DJANGO_PORT (127.0.0.1:8000)
curl -s 127.0.0.1:8000/api/v1/health    # {"status":"ok"}
```

`uv run python manage.py runserver` also works (ASGI through Daphne, with auto-reload, including the `/ws` socket).

**After every `git pull` run `uv run python manage.py migrate --database=migrator` again**: the test suite uses its own throwaway database, so the development database does not move on its own and silently lags behind new migrations (it was three migrations behind at the end of Epic 1). `scripts/dev_server.sh` starts the server but never migrates.

Mailpit UI: http://127.0.0.1:8025 shows every email the app sends locally (see `docs/mail.md`; `scripts/mail_check.py` sends one test message). Never commit `.env`; only `.env.example` (placeholders) is tracked.

## Database roles

| Role | Rights | Used by |
| --- | --- | --- |
| `migrator` | owns the database, runs DDL, may create test databases | `migrate`, tests |
| `runtime` | SELECT, INSERT, UPDATE, DELETE and sequence usage only; no DDL | the `default` connection in local and prod |
| `purger` | SELECT and DELETE on the history tables of `PURGE_ROLE_TABLES`, nothing else | `run_retention`, `purge_audit_records` (the `purge` connection) |

A database made before the `purger` role needs it added once: see `docs/audit.md`.

Roles are created by `docker/postgres/create_roles.sh` (compose init, and CI against a service container).
The compose init script only runs on an empty volume; to rotate a password or re-apply roles on an existing
database, re-run `docker/postgres/create_roles.sh` (it creates missing roles and resets passwords) or recreate the volume.
Always migrate with `--database=migrator`; plain `migrate` runs as `runtime` and is refused.
After each migration, `post_migrate` grants DML on all tables to `runtime`; run
`python manage.py grant_runtime_privileges` to re-apply. Tables that must be append-only
go in the `RUNTIME_APPEND_ONLY_TABLES` setting (the only route, so every migrate honours it);
the few columns of such a table that may still be updated go in `RUNTIME_COLUMN_UPDATES`.

## Settings

`config.settings.{local,test,prod}`, all driven by environment variables. Prod refuses to start when
`DJANGO_SECRET_KEY` or `RUNTIME_PASSWORD` is missing, a placeholder, or under 32 characters. The serving process
needs no DDL credentials: prod defines the `migrator` alias only when `MIGRATOR_PASSWORD` is set (validated the
same way), which only the migration job should provide.
Security baseline: `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `FRONTEND_BASE_URL` and `TRUSTED_PROXIES` come from
the environment (see `.env.example`). Wildcards are refused; prod also refuses a missing host list or a
`FRONTEND_BASE_URL` that is not an `https` origin without a path. Absolute links use `apps.kernel.links.frontend_url`
(never the Host header); `apps.kernel.network.client_ip` trusts `X-Forwarded-For` only from `TRUSTED_PROXIES`
(empty trusts none). Every response carries security headers and an API-only CSP; HSTS is prod-only. The Django
admin is not installed.
`CSRF_TRUSTED_ORIGINS` lists extra origins (prod needs https only); the `FRONTEND_BASE_URL` origin is always added to it, so
the list may be empty when the frontend proxy is the only caller. `TRUSTED_PROXIES` must be the proxy's own address range, never `0.0.0.0/0` (ranges wider than
/8 IPv4 or /16 IPv6 are refused).
`DJANGO_SETTINGS_MODULE` must be set for ASGI (`scripts/dev_server.sh` defaults it to local).

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run lint-imports           # layering and import contracts (pyproject.toml)
uv run pytest                 # needs the compose database running and .env present
uv run pytest -n 3            # whole suite in parallel (pytest-xdist, one database per worker)
uv run pytest tests/<app>     # just the affected app; `-m guard` runs the architecture, authz, privacy and OpenAPI guards
scripts/test-changed.sh       # guards plus the tests of whatever changed against main
uv audit
```

See `docs/testing.md` for the test selection, the `guard` marker and the parallel-run rules.

## Architecture checks

`apps/` holds one package per domain, layered bottom to top: `kernel`, `catalog`, `identity`, `listings`,
tier 4 (`valuation`, `search`, `assistant`, `messaging`), `governance`, `presentation`. `uv run lint-imports`
enforces the contracts in `pyproject.toml`: downward-only layers, independent tier-4 peers, no importing another
app's `models`, no importing any `subscribers` module (only an app's own `apps.py` may), and `assistant` never
touching `listings.models`. `tests/architecture/` proves each rule fails on a deliberate violation and rejects
`fields = "__all__"` in serializers. Event handlers live in each app's `subscribers` module, imported only by that app's own `apps.py` `ready()`.
New apps are added to `INSTALLED_APPS` only when they own models.
`scripts/asgi_thread_spike.py` re-runs the AD-14 sync-view concurrency spike; results are in `docs/architecture-notes.md`.

## Listings and the Field registry

`apps/listings` owns the canonical `PropertyListing` (Story 2.1: start a Draft; Story 2.2: `PATCH /api/v1/listings/{id}`
with `{base_revision, fields}` saves title, description, Asking price, price basis, rental period, (Story 2.3) the area, its unit and the marla
basis (the derived square feet are computed server-side), (Story 2.4) the House and Apartment details, in a lazily created `ListingResidential` row, (Story 2.5) the Plot, Plot File, Shop and Office details, in lazily created `ListingPlot` and `ListingCommercial` rows (the unit-details table also holds the fields Shops and Offices share with homes), and (Story 2.6) the sale and rent terms (negotiable price, possession availability, deposit, availability date, lease, utilities, pets, occupants) as typed nullable columns on `ListingTerms`, through
`listings.services.apply_updates`, the only write path for Listing fields: a stale `base_revision` is `409 stale_revision`
with the current Listing under `current`, every field carries `provenance` set by the service, every change writes a
`ListingRevision`, and contact details in text are refused) and `apps/catalog` owns the Field registry,
`apps/catalog/registry/fields.yaml` (version 6, field types `enum`, `text`, `money`, `decimal`, `integer`, `boolean`, `date`; the flag `seller_declared` and the map `allowed_values_by_subtype`), validated at start-up and served at
`GET /api/v1/registry`. Editing the registry needs a version bump and `uv run python scripts/registry_lock.py`. See
`docs/listings.md`, `docs/registry.md` and `docs/privacy-patterns.md`.

## API contract

The contract is the committed `openapi.yaml` (drf-spectacular, served at `/api/v1/schema`). After changing any
endpoint, serializer or error code, regenerate it; `tests/api/test_openapi.py` fails when it is stale:

```bash
uv run python manage.py spectacular --validate --file openapi.yaml
```

Errors use `{error:{code,message,fields,request_id}}` with the catalogue in `apps/kernel/errors.py`; messages are
static so submitted values never appear. Every response carries `X-Request-ID`, and request logs are JSON lines with an
allow-list of fields (no bodies, query strings, headers, cookies or exception messages).

## Authorisation

Every request resolves to one immutable `Actor` (visitor, user, admin or system; `apps/kernel/actor.py`) through the
single resolver in `apps/presentation/api/actors.py`; a Visitor is the fail-closed default until sign-in exists
(Story 1.17). Services take `actor` first and enforce policy themselves; tests enforce the actor-first signature
(`tests/architecture/test_service_signature.py`) and the ban on HTTP imports in domain apps, while
not reading request state is a rule reviewers uphold. Views read the caller with `get_actor(request)`. DRF denies by default, so each view declares `AllowPublic`,
`RequireSignedIn` or `RequireAdmin`; `tests/authz/test_routes.py` fails any route that does not. A Visitor on a
sign-in route gets 401 `unauthenticated`; a non-admin on an admin route and any unauthorised resource get 404
`not_found`. Domain apps may not import `django.http`, `rest_framework` or `channels` (import-linter). The route table
in `docs/authorisation-matrix.md` is generated (`uv run python scripts/authz_matrix.py`) and a test fails when stale.

Note: `/api/v1/schema` is public and must be gated before any non-local deployment (tracked in `deferred-work.md`).

## Rate limits

Limits are policy rows with PostgreSQL fixed-window counters; see `docs/rate-limits.md` for the table, semantics and how an endpoint adopts a scope.

## Domain events and idempotency

Events dispatch after commit, one callback per subscriber registered in `AppConfig.ready`; mutating services claim an idempotency key inside their transaction. See `docs/events.md` for the conventions, a registration sample and when to use AD-23 instead of an event.

## Runtime executor, flags, AI log and retention

A bounded executor keeps capacity free for chat and sign-in (`RUNTIME_EXECUTOR_MAX_WORKERS`, default 4, and `RUNTIME_EXECUTOR_RESERVED_WORKERS`, default 1), feature flags are declared in code, the AI request log holds no content, and `run_retention [--dry-run]` runs every app-registered purge job. See `docs/runtime-and-retention.md` for usage, how an app registers a flag or a retention job, and the deployment schedule note.

## Media and image uploads

Story 2.9 adds the image pipeline: `kernel.storage` (a storage port with a filesystem adapter rooted at `MEDIA_ROOT`,
an absolute path that defaults to the git-ignored `./media`; tests use a temporary directory), `kernel.images`
(Pillow, imported only in `kernel`; validates, strips all metadata and re-encodes on the runtime executor) and
`kernel.media` (per-account storage cap, 200 MB Individual and 1 GB Agency, orphan retention). Draft owners upload up to
10 images per Listing at `POST /api/v1/listings/{id}/images`. Pillow's wheels bundle their image libraries, so no system
packages are needed. Story 2.10 adds the cover flag and a consistent 0-based order to `ListingImage`, the
revision-checked `PATCH /api/v1/listings/{id}/images` (full order and optional cover), `media` entries on the Listing
resource, and the single media endpoint `GET /api/v1/listings/{id}/images/{image_id}/file`, which serves a Draft's image
only to its owner (everyone else gets the same 404; public serving comes with Story 2.12). Story 2.11 reuses the same
pipeline for profile pictures and Agency logos: `PUT`/`DELETE /api/v1/me/profile/image` (Individual) and
`/api/v1/me/agency-profile/image` (Agency) upload, replace or remove one image (6 MiB body, 5 MB image, 10 uploads per
hour, the profile PATCH's account-state gate), the owner reads it at `.../image/file` (`private, no-store`), the profile
answers carry `image`, and an Agency's logo is public at `GET /api/v1/agencies/{agency_id}/logo` only while the Agency
account is Active (`public, max-age=60`; everything else is the same 404). An Individual's picture is never public.
Deactivating an account removes its picture or logo. See `docs/media.md`.

## Audit trail

Security-sensitive actions are recorded in an append-only table through `kernel.audit`, in the caller's transaction, with allow-listed metadata; administrator reads of private data call `record_private_read` first with a per-read reason. See `docs/audit.md`. Stories 1.12 and 1.18 write the account-state and password-change records through this writer.

Signed-in accounts read their own in-app notifications at `GET /api/v1/notifications` (newest first, cursor pagination, `state=unread|all`), `GET /api/v1/notifications/unread-count`, `POST /api/v1/notifications/{id}/read` and `POST /api/v1/notifications/read-all` (CSRF; another account's id is `404`). Features raise them with `kernel.notifications.notify(account_id, kind, payload)` for a kind registered in `AppConfig.ready` with a strict payload schema; the row is stored after commit and never when the transaction rolls back. Password and email changes ship as the first two kinds; read notifications are purged after 90 days and any after 180 by the `kernel.notifications` retention job. See `docs/notifications.md`.

## Reference data

The amenity catalogue (`GET /api/v1/reference/amenities`, Story 2.8) works the same way; see `docs/reference-data.md`.

Cities live in the `catalog` app (seeded with Karachi) and are served as plain strings at `GET /api/v1/reference/cities`, never as OpenAPI enums. See `docs/reference-data.md` for how they are managed and where localities arrive.

## Accounts

The `identity` app owns the account table. The account state (`email_unverified`, `active`, `suspended`, `deactivated`) is derived from separate conditions, `identity.policy.can(actor, capability)` implements the FR-74 capability matrix (capabilities that depend on later stories are pending), state changes are audited services, and `lock_account` and `is_active_owner` are the lock and eligibility primitives. See `docs/account-states.md`.

## Contact-detail patterns

`apps.kernel.privacy.patterns` is the only definition of phone, email and private-address patterns; features and AI redaction call `reject_contact_details(text)` or `find_contact_details(text)` and never write their own contact regex (a repository test enforces it). See `docs/privacy-patterns.md`.

## Registration

`POST /api/v1/registrations/individuals` registers an Individual account (Email-unverified, with an individual profile) and answers the same `202` for a new and an existing email; an existing address gets an "already registered" notice instead. Rules and codes are shared by the service and the API; the phone is normalised to E.164 by `normalise_phone` in the patterns module and kept private. See `docs/registration.md`.

`POST /api/v1/registrations/agencies` registers an Agency account (agency name, business email, business phone, office address, city from the City reference list, password) with agency verification status `unverified`. It shares the Individual endpoint's rate-limit scope, generic `202` and verification flow; the business phone and office address stay private and `GET /api/v1/me` gains only the public `agency_name`. See `docs/registration.md`.

## Email verification

Registration queues a single-use, 24-hour verification link (after commit, through the mail port). `POST /api/v1/email-verifications/confirm` consumes it and moves the account to Active; `POST /api/v1/email-verification-requests` asks for a new link and always answers the same `202`. Tokens are stored hashed; expired and used ones are purged after 7 days by the `identity.email_verification_tokens` retention job. See `docs/email-verification.md`.

## Email verification and sign-in

`POST /api/v1/session` signs in (generic `401 invalid_credentials` for every failure, a per-email-and-network failure throttle that never uses 429), `DELETE /api/v1/session` signs out, `GET /api/v1/me` returns the actor summary and `GET /api/v1/csrf` returns the CSRF token the SPA must send as `X-CSRFToken`. Sessions are server-side: an HttpOnly, SameSite=Lax `estate_session` cookie holds a random token and only its hash is stored; they last 14 days absolute and 24 hours idle, and end on sign-out, password change and deactivation. `CSRF_TRUSTED_ORIGINS` always includes `FRONTEND_BASE_URL`. See `docs/sessions.md`.

## Password reset and change

`POST /api/v1/password-reset-requests` always answers the same `202` (unknown, unverified, suspended, deactivated and capped addresses alike; the 429 is per client, the per-account cap is silent) and queues a single-use, 1-hour link after commit. `POST /api/v1/password-resets/confirm` sets a new password with that link, ends every session of the account and writes `account.password_reset`; a bad password is a field error that leaves the link usable. `POST /api/v1/me/password` (signed in, CSRF) changes the password with the current one, ends the other sessions, keeps the current one and writes `account.password_changed`; wrong current passwords are throttled silently (never 429). Tokens are stored hashed and purged after 7 days by the `identity.password_reset_tokens` retention job. See `docs/password-reset.md`.

## Profile and email change

`GET`/`PATCH /api/v1/me/profile` (Individual only, `PATCH` with CSRF) read and update the name and phone; the phone stays mandatory and is returned only there, to its owner, and an update that changes something writes `account.profile_updated`. `POST /api/v1/me/email-change` (signed in, CSRF, not Admin) needs the current password under the shared password-change failure throttle and always answers the same `202`; a single-use 24-hour link goes only to a free new address (a registered one gets the "already registered" notice instead) and the old address is notified. `POST /api/v1/email-changes/confirm` (public) swaps the address, verifies it, ends no session and writes `account.email_changed`. Tokens are stored hashed and purged after 7 days by the `identity.email_change_tokens` retention job. See `docs/profile.md`.

`GET`/`PATCH /api/v1/me/agency-profile` (Agency only, `PATCH` with CSRF) read and update the agency name, a new optional public `description`, city, business phone and office address; name, phone, address and city stay mandatory, the phone and address are private to the owner, and phone numbers, email addresses or street addresses in the description are refused (`contains_contact_details`). The email change above works unchanged for agencies. See `docs/profile.md`.

## Administrators

Administrators exist only through `python manage.py provision_admin <create|disable|remove|reset-password> --email <address>`; the password is read from a hidden prompt or `--password-stdin`, never an argument, and every action is audited (`admin.provisioned`, `admin.disabled`, `admin.removed`, `admin.password_reset`). No default credential exists anywhere. They sign in at `POST /api/v1/admin-session` (strong 12-character password policy, own failure throttle `admin_sign_in`, 30 minutes idle and 8 hours absolute sessions, `admin.signed_in` and `admin.sign_in_failed` audit records) and the protected base is `/api/v1/admin/*` (`GET /api/v1/admin/me` today). A forgotten administrator password is recovered only with the command. See `docs/administrators.md`.

## Sample data

`python manage.py seed_sample_data [--reset] [--reseed] [--yes] [--password-stdin] [--listings N]` loads a sample Karachi marketplace for local development: 14 sample accounts (on the reserved `sample.estate-ai.invalid` domain), Listings with drawn pictures, and the messages, reports, verification and other activity around them. It is safely repeatable, removable with `--reset`, and refused under the production settings. No default password exists: it is read from the prompt or `--password-stdin`, or generated and printed once. See `docs/sample-data.md`.

## Mail

Features send email only through `apps.kernel.mail.send(MailMessage(...))` inside their transaction; it is delivered after commit, off the request thread, never retried, and links come from `email_link` (`FRONTEND_BASE_URL`). Local mail goes to Mailpit only. See `docs/mail.md`.
