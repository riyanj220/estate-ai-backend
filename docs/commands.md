# Command guide

Everything needed to run, test and maintain the project locally. **BE** means run it in `estate-ai-backend`, **FE** in
`estate-ai-frontend`. Add `--help` to any `manage.py` command to see its options.

## 1. Every day

| What | Where | Command |
| --- | --- | --- |
| Start database and mail catcher | BE | `docker compose up -d --wait` |
| Start the backend (real AI) | BE | `AI_PROVIDER=gemini uv run python manage.py runserver` |
| Start the backend (fake AI, no key or quota used) | BE | `uv run python manage.py runserver` (uses `AI_PROVIDER` from `.env`, normally `stub`) |
| Start the frontend with AI screens | FE | `AI_FEATURES=1 npm run dev` |
| Stop database and mail (data is kept) | BE | `docker compose down` |

- App: <http://localhost:3000> (use `localhost`, not `127.0.0.1`)
- Admin: <http://localhost:3000/admin>
- Emails the app sends: <http://127.0.0.1:8025>

## 2. After pulling or when code changed

| When | Where | Command |
| --- | --- | --- |
| Always after a pull (new migrations) | BE | `uv run python manage.py migrate --database=migrator` |
| Python dependencies changed | BE | `uv sync --frozen` |
| Node dependencies changed | FE | `npm ci` |
| "Permission denied" on a table | BE | `uv run python manage.py grant_runtime_privileges` |

Always migrate with `--database=migrator`. A plain `migrate` is refused.

## 3. First-time setup (new machine or empty database)

Run in this order.

| Step | Where | Command |
| --- | --- | --- |
| Create `.env` with random secrets (only if there is none) | BE | `python3 scripts/bootstrap_env.py` |
| Start services | BE | `docker compose up -d --wait` |
| Install dependencies | BE | `uv sync --frozen` |
| Create the tables | BE | `uv run python manage.py migrate --database=migrator` |
| Register the price model | BE | `uv run python manage.py register_valuation_model` |
| Build the assistant's knowledge index | BE | `AI_PROVIDER=gemini uv run python manage.py rebuild_knowledge_index` |
| Create an administrator | BE | `uv run python manage.py provision_admin create --email admin@example.com` |
| Load the sample marketplace (after the price model) | BE | `uv run python manage.py seed_sample_data` |
| Install frontend dependencies | FE | `npm ci` |

For the real AI, add `GEMINI_API_KEY=...` to `.env` yourself. Never commit or share `.env`.

## 4. Sample data

| What | Command (BE) |
| --- | --- |
| Load the sample marketplace, or add what is missing (40 listings) | `uv run python manage.py seed_sample_data` |
| Delete the sample data and create it again | `uv run python manage.py seed_sample_data --reseed --yes` |
| More listings (1 to 120) | `uv run python manage.py seed_sample_data --reseed --yes --listings 80` |
| Delete the sample data only | `uv run python manage.py seed_sample_data --reset --yes` |

A run takes about a minute. Use `--reseed --yes` if your database still has an older, smaller set of sample data.

Accounts are `sample.individual.1..8@sample.estate-ai.invalid` and `sample.agency.1..6@sample.estate-ai.invalid`. They
share one password: you are asked for it, or a random one is printed once. Start with `sample.agency.1` (verified seller
with a busy inbox) and `sample.individual.1` (buyer); `sample.individual.8` is empty. The full list and what is created
are in `sample-data.md`. `--reset` leaves your own accounts alone, but removes your conversations and reports that
involve a sample account.

## 5. Administrator

All are `uv run python manage.py provision_admin <action> --email <address>` (BE).

| Action | Use |
| --- | --- |
| `create` | New administrator |
| `reset-password` | Forgotten password |
| `reset-second-factor` | Lost authenticator app |
| `disable` / `enable` | Turn an administrator off or on |
| `remove` | Delete the administrator |

`--force-last` is needed to disable or remove the last active administrator.

## 6. Tests and checks

### Backend

| What | Command (BE) |
| --- | --- |
| Only what changed against `main` | `scripts/test-changed.sh` |
| One area | `uv run pytest tests/listings` |
| Whole suite (about 8 minutes) | `uv run pytest -n 3` |
| Architecture, permission and privacy guards | `uv run pytest -m guard` |
| Speed budgets | `uv run pytest -m perf -s` |
| Style | `uv run ruff check . && uv run ruff format --check .` |
| Layer rules | `env -u DJANGO_SETTINGS_MODULE uv run lint-imports` |
| Missing migrations | `uv run python manage.py makemigrations --check --dry-run` |
| Regenerate the API description | `uv run python manage.py spectacular --validate --file openapi.yaml` |

If your shell sets `DJANGO_SETTINGS_MODULE`, put `env -u DJANGO_SETTINGS_MODULE` before `uv run pytest`.
`scripts/smoke.py` tests a running server and creates data, so use a throwaway database (steps in `testing.md`).

### Frontend

| What | Command (FE) |
| --- | --- |
| Style and types | `npm run lint && npm run typecheck` |
| Quick browser tests | `PW_CHANNEL=chrome npm run test:e2e:smoke` |
| Smoke plus changed specs | `PW_CHANNEL=chrome npm run test:e2e:changed` |
| One spec file | `PW_CHANNEL=chrome npm run test:e2e -- moderation.spec.ts` |
| Full suite (slow; CI does this) | `PW_CHANNEL=chrome npm run test:e2e` |
| After a backend API change | `npm run api:sync` |
| Check the API client is current | `npm run api:check` |
| Production build | `npm run build` then `npm run start` |

Browser tests use a fake backend, so they do not prove the two repositories work together. Run one at a time.

## 7. Maintenance jobs

All BE. Nothing schedules these yet.

| What | Command |
| --- | --- |
| Refresh duplicate and suspicious listing flags | `uv run python manage.py run_flags` |
| See what data retention would delete | `uv run python manage.py run_retention --dry-run` |
| Delete expired data | `uv run python manage.py run_retention` |
| See what the audit purge would delete | `uv run python manage.py purge_audit_records --dry-run` |
| Delete audit records older than 24 months | `uv run python manage.py purge_audit_records` |

Real deletion of history needs the `purger` database role (one-time setup in `audit.md`).

## 8. AI and price model (only when you change them)

| What | Command (BE) |
| --- | --- |
| Check the AI provider works (uses quota) | `AI_PROVIDER=gemini uv run python manage.py ai_provider_trial` |
| After editing knowledge documents | `uv run python scripts/knowledge_lock.py` then rebuild the index (section 3) |
| Measure assistant knowledge accuracy | `AI_PROVIDER=gemini uv run python manage.py evaluate_knowledge` |
| Measure natural-language search (uses quota) | `AI_PROVIDER=gemini uv run python manage.py evaluate_search --limit 10 --pause 5` |
| Export the synthetic price dataset as CSV | `uv run python manage.py export_valuation_dataset` |
| Train a new price model | `uv run python manage.py train_valuation_model --model-version 2` |
| Start using the newest model | `uv run python manage.py register_valuation_model` |
| After editing listing fields (`fields.yaml`) | `uv run python scripts/registry_lock.py` |
| After adding or changing routes' permissions | `uv run python scripts/authz_matrix.py` |

The knowledge index must be built with the same `AI_PROVIDER` the server uses: rebuilding with `stub` replaces the
Gemini index. Use a model version number that does not exist yet.

## 9. Start over (destroys all local data)

This deletes your whole local database, including your own accounts. Uploaded files in `media/` stay behind.

```bash
docker compose down -v
```

Then follow section 3, skipping the `.env` step.
