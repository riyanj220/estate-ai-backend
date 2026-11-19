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
scripts/dev_server.sh                   # Daphne on DJANGO_BIND_HOST:DJANGO_PORT (127.0.0.1:8000)
curl -s 127.0.0.1:8000/api/v1/health    # {"status":"ok"}
```

Mailpit UI: http://127.0.0.1:8025. Never commit `.env`; only `.env.example` (placeholders) is tracked.

## Database roles

| Role | Rights | Used by |
| --- | --- | --- |
| `migrator` | owns the database, runs DDL, may create test databases | `migrate`, tests |
| `runtime` | SELECT, INSERT, UPDATE, DELETE and sequence usage only; no DDL | the `default` connection in local and prod |

Roles are created by `docker/postgres/create_roles.sh` (compose init, and CI against a service container).
The compose init script only runs on an empty volume; to rotate a password or re-apply roles on an existing
database, re-run `docker/postgres/create_roles.sh` (it creates missing roles and resets passwords) or recreate the volume.
Always migrate with `--database=migrator`; plain `migrate` runs as `runtime` and is refused.
After each migration, `post_migrate` grants DML on all tables to `runtime`; run
`python manage.py grant_runtime_privileges` to re-apply. Tables that must be append-only
go in the `RUNTIME_APPEND_ONLY_TABLES` setting (the only route, so every migrate honours it).

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
`CSRF_TRUSTED_ORIGINS` is separate from `FRONTEND_BASE_URL`: prod needs https origins only and it may be empty when the
proxy is same-origin. `TRUSTED_PROXIES` must be the proxy's own address range, never `0.0.0.0/0` (ranges wider than
/8 IPv4 or /16 IPv6 are refused).
`DJANGO_SETTINGS_MODULE` must be set for ASGI (`scripts/dev_server.sh` defaults it to local).

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run lint-imports           # layering and import contracts (pyproject.toml)
uv run pytest                 # needs the compose database running and .env present
uv audit
```

## Architecture checks

`apps/` holds one package per domain, layered bottom to top: `kernel`, `catalog`, `identity`, `listings`,
tier 4 (`valuation`, `search`, `assistant`, `messaging`), `governance`, `presentation`. `uv run lint-imports`
enforces the contracts in `pyproject.toml`: downward-only layers, independent tier-4 peers, no importing another
app's `models`, no importing any `subscribers` module (only an app's own `apps.py` may), and `assistant` never
touching `listings.models`. `tests/architecture/` proves each rule fails on a deliberate violation and rejects
`fields = "__all__"` in serializers. Event handlers live in each app's `subscribers` module, imported only by that app's own `apps.py` `ready()`.
New apps are added to `INSTALLED_APPS` only when they own models.
`scripts/asgi_thread_spike.py` re-runs the AD-14 sync-view concurrency spike; results are in `docs/architecture-notes.md`.

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

## Audit trail

Security-sensitive actions are recorded in an append-only table through `kernel.audit`, in the caller's transaction, with allow-listed metadata; administrator reads of private data call `record_private_read` first with a per-read reason. See `docs/audit.md`. Stories 1.12 and 1.18 write the account-state and password-change records through this writer.

## Reference data

Cities live in the `catalog` app (seeded with Karachi) and are served as plain strings at `GET /api/v1/reference/cities`, never as OpenAPI enums. See `docs/reference-data.md` for how they are managed and where localities arrive.

## Accounts

The `identity` app owns the account table. The account state (`email_unverified`, `active`, `suspended`, `deactivated`) is derived from separate conditions, `identity.policy.can(actor, capability)` implements the FR-74 capability matrix (capabilities that depend on later stories are pending), state changes are audited services, and `lock_account` and `is_active_owner` are the lock and eligibility primitives. See `docs/account-states.md`.
