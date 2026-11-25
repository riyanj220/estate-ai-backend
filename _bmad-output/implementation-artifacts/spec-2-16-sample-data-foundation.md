---
title: 'Story 2.16: Sample data foundation (backend only)'
type: 'feature'
created: '2026-11-25'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Story 2.16)'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-7, AD-22, AD-23)'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-15-validate-and-publish-a-listing.md'
  - '{project-root}/apps/identity/management/commands/provision_admin.py'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/testing.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every later frontend and test story needs realistic published Listings and accounts, and there is no way to create them except by hand.

**Approach:** A `seed_sample_data` management command that, as the system actor, creates a small set of Individual and Agency accounts and published Listings of several Categories, publishing each through `listings.publication.publish` with the `seed` event kind (which consumes no quota). It is safe to repeat, identifiable internally, and can remove or reseed its own records. Later epics extend it. Backend only. Workflow: narrow local runs (`docs/testing.md`), full suite in CI; decide ambiguities yourself with the best option and report them.

## Boundaries & Constraints

**Always:**
- **Command**: `python manage.py seed_sample_data [--reset] [--reseed] [--password-stdin] [--listings N]`. Default (no flag): create what is missing (idempotent: a second run changes nothing and creates nothing). `--reset`: remove every record the command created and nothing else. `--reseed`: reset then create. It refuses to run when the settings are the production settings (check the settings module / `DEBUG` / an explicit `SEED_ALLOWED` style guard consistent with `config/settings/prod.py`; the refusal is one line and a non-zero exit) and refuses unless an explicit setting or flag allows it outside local/test settings. Output is plain counts, never passwords, emails of real people or any private value.
- **Passwords**: seeded accounts need a password to be usable in the UI. Never a default or a literal in the repo (secret scanning). The password is read from a hidden prompt or one stdin line with `--password-stdin` (like `provision_admin`), applied to all seeded accounts; if neither is given and accounts need creating, generate a random one with `secrets`, print it once at the end ("seed accounts password: ...") and never log it. A run that creates no account needs no password.
- **Identifiable records**: seeded accounts use a reserved address domain that cannot receive mail (`@sample.estate-ai.invalid` or similar reserved TLD; document it); every seeded listing belongs to a seeded account; `--reset` deletes by that rule through the owning services (listings with their images via the delete path that gives storage usage back, audit rows are kept as audit, publication events are removed only by the command's privileged path (see below), accounts through the identity service). The command never touches a non-seeded record, even one that looks similar; test this with a non-seed account and listing present.
- **Accounts**: 3 Individual and 2 Agency accounts, Active and email-verified, with profiles filled enough for the UI (Individual full name; Agency business name and a city), created through `identity.services` as the system actor (use the real creation/registration services and the existing verification mechanism or the narrowest existing service that yields an Active verified account; do not write rows by hand). Distinct, obviously fake names. No phone numbers or addresses that look real.
- **Listings**: about 12 (override with `--listings`, bounded 1..60) published Listings spread across purposes (sale and rent), Categories (house, apartment, plot, commercial) and Subtypes, Karachi city and its seeded localities, with realistic titles/descriptions (no contact details), valid prices/areas/units per the registry, bedrooms/bathrooms where required, public location text, a private address on some (so private-field tests have data), some amenities and nearby facilities, and 1..3 generated images each (small valid PNG/JPEG generated in code with Pillow through the normal `listings.services.upload_image` path as the owner actor, so image rows, usage and storage files are real; clean up the storage files on reset). Data comes from a deterministic table in code (a fixed list, optionally seeded `random.Random(2026)`) so runs are reproducible. Every listing is created as a Draft with `apply_updates` (the normal write path, `origin` manual) and published through `publication.publish`.
- **`seed` event kind**: extend `listings.publication.publish` with a keyword-only `kind` parameter (default `initial`; the only other allowed value is `seed`). `kind=seed` is accepted ONLY for the system actor (`ActorKind.SYSTEM`); any other actor passing it is refused (`forbidden`, tested) and a non-system caller can never reach it over HTTP (the view never passes it). A `seed` event is written with `counted=False` (the CHECK already allows it), consumes no quota and does not need quota available; it still validates the Draft completely, claims an idempotency key (derive a stable per-listing key so a repeat is a replay), locks account then listing, compares `base_revision`, writes the audit record and emits `listing.published` after commit. Account-state refusals still apply (seed accounts are Active, so they pass). Because seeded events do not count, seeded accounts keep their full real allowance (test: after seeding, `allowance_for` reports used 0 for a seeded account).
- **Reset of events**: the runtime role may only INSERT and SELECT `listings_publicationevent` (2.14 grants). `--reset` must remove seed events too, because the accounts go away. Decide the cleanest way: run the command's database work through the migrator/privileged connection alias that other maintenance commands use (check `provision_admin`, retention, and settings), or, if no such alias fits, leave the event rows (they have no foreign keys and are harmless history) and document it. Whatever you choose, document it in `docs/listings.md`, make `--reset` followed by a seed work repeatedly without unique-key clashes (event idempotency keys for seeds must be derived so they cannot collide after a reset: include a per-run nonce or delete the old events), and test it.
- **Safety and docs**: the command is a developer tool: add `docs/sample-data.md` (what it creates, how to run, reset, the reserved domain, the password handling, that it refuses in production, that later epics extend it, and how tests use it), mention it in the README, add it to `docs/testing.md` if tests use it. Do not add it to any default migration or startup path. A small public helper module `apps/listings/sample_data.py` (or a dedicated app-less module under `apps/listings/management`) holds the data table and the seed functions so tests can call them without the CLI; the command is a thin wrapper. Respect the import-linter contracts (listings may use identity services/selectors and catalog selectors; the command must not import another app's models).
- **Tests (narrow runs)**: a first run creates 5 accounts and the requested number of published listings with images; the second run creates nothing; `--reset` removes only seeded records (a non-seed account/listing/image stays, a similarly named one stays); `--reseed` works twice in a row; seeded listings are public under the 2.12 policy (`public_listing` returns them, private address absent) across several categories and both purposes; seeded accounts have allowance used 0 and a later real publication by a seeded account consumes quota normally; `seed` kind: system actor only (a USER actor passing `kind=seed` is refused and no event is written), `counted=False`, idempotent replay, validation still enforced (an invalid draft is refused even for seed), the HTTP publish route cannot produce a seed event; refusal in production settings; password handling (stdin, prompt not needed, random printed once and not in logs, nothing logged); counts-only output with no email/password leakage; determinism (two runs with the same size produce the same titles); the command output and logs contain no private address values. Mutation-check the system-only guard and the idempotency.

**Never:** put a password or real-looking personal data in the repo, logs or output; let a non-system actor publish with `seed`; touch non-seeded records; run in production; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First run | empty database | 5 accounts, N published listings with images, counts printed | N/A |
| Second run | already seeded | nothing created or changed | N/A |
| `--reset` | seeded + non-seed data | only seeded records removed | N/A |
| `--reseed` twice | any | same end state each time, no key clashes | N/A |
| Production settings | prod | refused, non-zero exit | one-line error |
| `publish(kind=seed)` as system | valid draft | published, event `seed`, `counted=False`, no quota used | N/A |
| `publish(kind=seed)` as a user | any | `forbidden`, nothing written | envelope |
| Invalid draft via seed | missing title | refused like any publish | field errors |
| Password | none given, accounts needed | random printed once, never logged | N/A |

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

## Verification

**Backend (narrow while developing, full in CI):** `uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run lint-imports && uv run python manage.py makemigrations --check --dry-run && uv run python manage.py spectacular --validate --file openapi.yaml`, then `scripts/test-changed.sh` and, before the merge, one parallel full run `env -u DJANGO_SETTINGS_MODULE uv run pytest -n 3 -q`.
