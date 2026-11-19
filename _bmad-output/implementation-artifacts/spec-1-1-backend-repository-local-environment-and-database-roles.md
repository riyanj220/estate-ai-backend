---
title: 'Story 1.1: Backend repository, local environment and database roles'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'eeb83969b39352109c16040c0f00358b3a73b172'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The repository has planning artifacts but no runnable backend, local environment, safe configuration or database privilege model for later stories to build on.

**Approach:** Scaffold a minimal Django 5.2 project with split settings, a Docker Compose environment (PostgreSQL 18 + pgvector, dev mail catcher), separate migration and runtime database roles, a health endpoint, tests and CI.

## Boundaries & Constraints

**Always:** Python 3.12, Django 5.2 LTS, Daphne single process and worker, configuration only from environment variables; services published on 127.0.0.1 only; prod refuses to start on a missing or placeholder secret; no secret committed (`.env` ignored, `.env.example` holds placeholders only); dependencies hash-locked and audited; runtime database role has no DDL rights.

**Never:** Create the layered `apps/` skeleton or import contracts (Story 1.2), DRF, OpenAPI, error envelope or logging middleware (1.3), security headers, hosts or CSP (1.4), any frontend, any business model, or any migration beyond Django's built-ins.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Health, DB up | `GET /api/v1/health` | 200 `{"status":"ok"}` | N/A |
| Health, DB down | same request | 503 `{"status":"unavailable"}` | No connection details in body or logs |
| Prod, secret missing | prod settings, `DJANGO_SECRET_KEY` unset | Startup fails | `ImproperlyConfigured` names the variable, never a value |
| Prod, placeholder secret | secret equals `change-me` style value or is shorter than 32 chars | Startup fails | Same error |
| Runtime DDL | runtime role runs `CREATE TABLE` or `ALTER TABLE` | Refused | Postgres permission error |
| Runtime DML | runtime role selects, inserts, updates, deletes on a migrated table | Allowed | N/A |

</frozen-after-approval>

## Code Map

Greenfield: only `_bmad/`, `_bmad-output/`, `docs/` exist. Everything below is new; the spine's `config/` and `apps/` layout applies (`apps/` arrives in 1.2).

- `pyproject.toml`, `uv.lock`, `.python-version` -- runtime deps (django 5.2, daphne, psycopg 3) and dev group (pytest, pytest-django, ruff)
- `config/settings/{base,local,test,prod}.py`, `config/env.py` -- split settings; tiny `.env` reader for local/test
- `config/{asgi,urls,health}.py`, `manage.py` -- ASGI app, URL table, health view (project-level ops endpoint; may move to presentation in 1.2/1.3)
- `config/apps.py`, `config/management/commands/grant_runtime_privileges.py` -- post-migrate grants to the runtime role (DML only)
- `docker-compose.yml`, `docker/postgres/create_roles.sh`, `.env.example`, `scripts/bootstrap_env.py`, `scripts/dev_server.sh` -- environment, roles, local secret generation, Daphne launcher
- `tests/` -- health, prod-settings and DB-role tests
- `.github/workflows/ci.yml`, `README.md` -- CI and setup instructions

## Tasks & Acceptance

**Execution:**
- [x] `pyproject.toml`, `.python-version` -- project metadata, pinned Python 3.12, dependency groups; run `uv lock` so `uv.lock` carries hashes -- reproducible, audited installs
- [x] `config/env.py`, `config/settings/*.py` -- base/local/test/prod; `require_secret(name)` rejects missing, empty, placeholder or short values; `default` DB uses the runtime role in local/prod, the migrator in test -- safe configuration
- [x] `config/health.py`, `config/urls.py`, `config/asgi.py`, `manage.py` -- health view runs `SELECT 1`, returns the matrix responses -- ops endpoint
- [x] `docker-compose.yml`, `docker/postgres/create_roles.sh` -- `pgvector/pgvector:0.8.7-pg18-trixie` and Mailpit on 127.0.0.1; script creates `migrator` (LOGIN, CREATEDB, owns the database) and `runtime` (LOGIN, no CREATE) from env passwords; creates the `vector` extension in `template1` -- roles and extension
- [x] `config/apps.py`, `grant_runtime_privileges` command -- `post_migrate` grants SELECT, INSERT, UPDATE, DELETE and sequence usage to the runtime role; skips tables registered as append-only later -- DML-only runtime
- [x] `scripts/bootstrap_env.py`, `.env.example`, `scripts/dev_server.sh` -- generate random local secrets into the ignored `.env`; start Daphne from `DJANGO_BIND_HOST` and `DJANGO_PORT` -- no committed secrets
- [x] `tests/` -- cover every matrix row: health up and down, prod secret failures, runtime DDL refused and DML allowed
- [x] `.github/workflows/ci.yml`, `README.md` -- jobs: ruff, pytest with the pgvector service and role script, gitleaks secret scan, `uv audit`; README documents setup and `migrate --database` roles usage

**Acceptance Criteria:**
- Given a fresh clone with Docker, Python 3.12 and uv, when I follow the README, then `docker compose up` starts PostgreSQL 18 with pgvector and the mail catcher on 127.0.0.1 only and Daphne serves the project with one worker.
- Given prod settings, when a secret is missing or a placeholder, then startup fails naming the variable; no secret exists in git history or tracked files.
- Given the initialised database, when the runtime role attempts DDL, then it is refused; migrations succeed only as the migrator role.
- Given CI, when it runs, then lint, tests, secret scan and dependency audit pass.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | `uv.lock`/`.gitignore` missing from diff (BH, EC) | false | reject | Both exist and are tracked; the diff excluded `uv.lock`, and `.gitignore` is in the baseline commit. |
| 2 | Prod lacks ALLOWED_HOSTS, SECURE_*, logging (BH) | low | reject | Intent excludes it: spec Never lists hosts, headers and CSP (Story 1.4). |
| 3 | Prod falls back to local defaults for host/db/usernames (BH) | low | reject | A wrong host fails loudly at connect; fix adds guards. |
| 4 | `.env` written world-readable before chmod (BH, EC) | low | patch | Real race window; direct atomic 0o600 create. |
| 5 | `.env` parsed two ways; shell source overrides exported vars (BH, EC) | medium | patch | `dev_server.sh` overrides pre-exported `DJANGO_PORT`, unlike `load_dotenv`. |
| 6 | `local.py` uses `int(env(...))` for MAIL_PORT (BH, EC) | low | patch | Raw ValueError vs ImproperlyConfigured; direct correction. |
| 7 | Local accepts `change-me` placeholders, also in compose/role script (BH, EC) | low | reject | Local-only, loopback-only; fix adds guards. |
| 8 | `create_roles.sh` never updates passwords of existing roles; passwords in `psql -v` argv (BH, EC) | medium | patch (rotation); reject (argv) | Rotation claim real; argv exposure is inside an ephemeral container or CI runner. |
| 9 | `--append-only` flag undone by next `post_migrate` (BH, EC) | medium | patch | Verified: hook uses only the settings list. Remove the flag. |
| 10 | Sequence grant includes UPDATE (allows setval) (BH) | low | patch | Spec says usage only; direct correction. |
| 11 | No ALTER DEFAULT PRIVILEGES; custom schema unsupported (BH, EC) | low | reject | post_migrate covers Django-created tables; fix adds scope. |
| 12 | Append-only table not yet existing makes REVOKE raise (EC) | low | reject | Needs a settings entry ahead of its migration; guard adds a branch. |
| 13 | Role-name regex vs uppercase RUNTIME_USER (EC) | low | reject | Contrived; default is lowercase. |
| 14 | Health swallows errors with no log (BH, EC, VG) | low | patch | No operator signal on persistent 503; log exception class only. |
| 15 | Health rejects HEAD (EC) | low | patch | Probes often use HEAD; use `require_safe`. |
| 16 | No DB connect timeout; hang ties up the single Daphne worker (EC) | medium | patch | Real with one worker; add `connect_timeout`. |
| 17 | Prod web process requires and holds MIGRATOR_PASSWORD (EC) | medium | patch | Defeats least privilege if the web process is compromised; make the migrator alias optional in prod. |
| 18 | Default Django `auth`/`sessions` installed before Story 1.12's custom Account (found in triage) | medium | patch | Swapping AUTH_USER_MODEL after `auth` migrated breaks migration history; remove unused apps now. |
| 19 | `default` alias user not asserted in settings tests (VG) | medium | patch | Swapping the two DB builds keeps all tests green. |
| 20 | Placeholder/short rejection tested only for SECRET_KEY (BH, EC, VG) | medium | patch | Using `env` instead of `require_secret` on DB passwords passes all tests. |
| 21 | asgi no-default guard untested (BH, VG) | low | patch | Replacing it with setdefault keeps tests green. |
| 22 | post_migrate hook and append-only setting untested (VG) | medium | patch | No test references RUNTIME_APPEND_ONLY_TABLES; cheap to add now. |
| 23 | pgvector extension unverified (VG) | low | patch | One-line `pg_extension` assertion. |
| 24 | Health tested as migrator, not the runtime path (VG) | low | reject | Runtime reachability is covered by the role tests. |
| 25 | `load_dotenv`/bootstrap lack tests (VG) | low | reject | Low risk, simple code. |
| 26 | `.env` partial/stale; manage.py defaults to local settings (EC) | low | reject | Documented convention; fix adds branches. |
| 27 | Test probe row not cleaned if the test fails (EC) | low | reject | Fresh test database per run. |
| 28 | CI hygiene: action pins, timeouts, concurrency, cache (BH) | low | reject | Cosmetic for this story. |
| 29 | gitleaks-action licence for organisation repos (BH, EC) | maybe-false (low) | reject | Only affects organisation repositories; would be low. |
| 30 | README `uv audit` vs CI flag (BH, EC) | false | reject | Bare `uv audit` runs and passes locally. |
| 31 | Runtime can no-op or fake migrate until first DDL (EC) | low | reject | Documented behaviour; DDL is refused. |

## Design Notes

Decisions made without asking (not user-visible): Mailpit as dev mail catcher; ruff for lint; GitHub Actions with gitleaks (the proposal names GitHub); roles created by a shell script usable from compose init and CI; grants applied on `post_migrate` so test databases get them too; `.env` generated locally by script rather than committed defaults.

Role model: `migrator` owns the schema and runs migrations (`manage.py migrate --database=migrator` in local/prod); `runtime` serves requests. The audit table's UPDATE/DELETE revoke arrives in Story 1.10, so the grant command must support an append-only exclusion list.

## Verification

**Commands:**
- `docker compose up -d --wait` -- expected: db and mail healthy, ports bound to 127.0.0.1
- `uv sync --frozen && uv run ruff check . && uv run pytest` -- expected: all pass
- `uv audit` -- expected: no known vulnerabilities
- `uv run python manage.py migrate --database=migrator` then `curl -s 127.0.0.1:8000/api/v1/health` -- expected: `{"status":"ok"}`

**Manual checks (if no CLI):**
- `.env` is untracked; `git ls-files | grep -i env` shows only `.env.example`
