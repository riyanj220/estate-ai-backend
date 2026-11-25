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
| What changed against `main` | `scripts/test-changed.sh` (optionally `scripts/test-changed.sh <base-ref>`) |

## The `guard` marker

`-m guard` selects the architecture rules (`tests/architecture`), authorisation (`tests/authz`), privacy
(`tests/privacy`) and the OpenAPI freshness check (`tests/api/test_openapi.py`). They protect cross-cutting contracts,
so run them whenever you touch more than one app, a route, a serializer or a service signature. Mark a new guard test
file with `pytestmark = pytest.mark.guard`.

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
