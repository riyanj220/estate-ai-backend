# Running the backend tests

The full suite is ~4,900 tests and takes 7.5 to 10 minutes serially on a 4-core laptop (about 6 minutes with `-n 3`). Use the narrowest
selection that covers your change while developing, and the full parallel run before merging.

All commands need the compose database running and `.env` present, and are run with
`env -u DJANGO_SETTINGS_MODULE` if your shell exports another settings module (`pyproject.toml` sets the test one).

| Goal | Command |
| --- | --- |
| Whole suite, serial | `uv run pytest` |
| Whole suite, parallel | `uv run pytest -n 3` (or `-n auto`) |
| One app | `uv run pytest tests/listings` (apps/`<app>` maps to tests/`<app>`; HTTP views live in `tests/api`) |
| Guards only (about a minute) | `uv run pytest -m guard` |
| Performance budgets (about a minute) | `uv run pytest -m perf -s` (see `docs/performance.md`) |
| What changed against `main` | `scripts/test-changed.sh` (optionally `scripts/test-changed.sh <base-ref>`) |

## The `guard` marker

`-m guard` selects the architecture rules (`tests/architecture`), authorisation (`tests/authz`), privacy
(`tests/privacy`) and the OpenAPI freshness check (`tests/api/test_openapi.py`). They protect cross-cutting contracts,
so run them whenever you touch more than one app, a route, a serializer or a service signature. Mark a new guard test
file with `pytestmark = pytest.mark.guard`.

## The `perf` marker

Timing tests on the large fixture are deselected by default and run serially with `uv run pytest -m perf -s`. A `-m`
expression of your own replaces that default, so add `and not perf` to it unless you mean to run them.
`docs/performance.md` has the budgets, the fixture and the measured numbers.

## Privacy gates (Story 3.2)

Three `guard` tests in `tests/privacy` cover later endpoints without new test code (`audience.py` says who sees each
route, from the permissions of the authorisation matrix):

- `test_schema_walk.py`: no public or other-account response schema (`$ref`s resolved) has a private registry field or a
  private profile column, and no public one has a phone-like property.
- `test_canary_sweep.py`: canary phones, emails, addresses and coordinates are stored, every route is called as a
  Visitor and as two other accounts, and no response body or header may contain one.
- The same run captures every log record and searches it for the canaries.

When a gate fails on a new route:

- `cannot tell who sees <route>`: a signed-in route outside `/api/v1/me` must be declared `OWNER` or `OTHER_ACCOUNT` in
  `SIGNED_IN` (`tests/privacy/audience.py`).
- `cannot build a URL for <route>`: add a value for its new path parameter to `seed()` in `test_canary_sweep.py`.
- `no 200 from these public routes`: seed what the route needs to answer, so that it is really checked.

The sweep calls non-`GET` methods only on routes with a path parameter, with an empty body, and expects a refusal. It
sends no query parameters, so a response branch that needs one is not exercised.

## `scripts/test-changed.sh`

Lists files changed against the base ref (default `main`, plus untracked files), maps `apps/<app>/...` to
`tests/<app>`, `apps/presentation/...` to `tests/api` and `tests/authz`, and edited test files to their top-level test
directory. It always runs the guards, then the selected directories, in parallel (`PYTEST_WORKERS`, default 3). Changes
under `config/` or `scripts/` select the guards only, so run the full suite before merging those. Extra pytest
arguments go after `--`.

## Parallel runs (pytest-xdist)

Each xdist worker gets its own database, created by the migrator role from `template1` (so the `vector` extension is
present) as `test_<POSTGRES_DB>_gw0`, `_gw1`, and so on. Migrations seed the reference data and the `post_migrate` hook
grants the runtime role per database, so nothing needs setting up per worker. The migrator needs `CREATEDB`, which
`docker/postgres/create_roles.sh` gives it. Rules for tests that keep them parallel-safe:

- Parametrize ids must be deterministic: never put `uuid.uuid4()`, random values or timestamps in a parametrize list
  (workers refuse to start when they collect different test ids). Use `uuid.UUID(int=n)`.
- Do not rely on state outside the test's own database transaction or `tmp_path`/`media_root`; each worker is a
  separate process with its own database, so process-global state is isolated per worker.

## Sample data in tests

`apps/listings/sample_data.py` (`seed`, `reset`, `reseed`, `row_for`) is callable without the CLI; `tests/listings/test_sample_data.py` shows the setup (`seed_cap()` and `seed_media_policies()`). A 12-Listing run takes about 3 seconds and 60 about 13, so use `seed(5)` unless a test needs the spread across categories. See `docs/sample-data.md`.

## Real-stack smoke test

`scripts/smoke.py` drives the main flows over plain HTTP against a running server (cookies, CSRF, Mailpit links), which
the test suite does not: it uses the runtime database role, background work and a real server. It creates accounts and
Listings and changes (then restores) a quota limit, so run it only against a throwaway database:

1. In the Compose Postgres, create `estate_ai_smoke` owned by `migrator`, with the grants and extensions of
   `docker/postgres/create_roles.sh`.
2. Export `POSTGRES_DB=estate_ai_smoke`, a temporary `MEDIA_ROOT` and `AI_PROVIDER=stub`, then run
   `migrate --database=migrator`, `register_valuation_model` and `rebuild_knowledge_index`.
3. Start the server with the local settings on another port: `DJANGO_PORT=8100 scripts/dev_server.sh`.
4. In a shell with the same exports: `uv run python scripts/smoke.py --base-url http://127.0.0.1:8100 --provision-admin`.
5. Drop the database afterwards.

The base URL may also be a frontend that proxies to that server (the "live delivery" flow then proves the `/ws`
proxy too). Pass `--origin` with one of the server's trusted origins when it is not `http://localhost:3000`, and
`--live-idle 45` to check that an idle socket stays open. Registration is limited to 10 an hour per client and
one run uses three. With the stub provider the assistant extracts nothing and cannot write a description.
