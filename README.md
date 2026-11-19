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
`DJANGO_SETTINGS_MODULE` must be set for ASGI (`scripts/dev_server.sh` defaults it to local).

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest                 # needs the compose database running and .env present
uv audit
```
