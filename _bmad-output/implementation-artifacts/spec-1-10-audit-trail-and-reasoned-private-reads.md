---
title: 'Story 1.10: Audit trail and reasoned private reads'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '0a6349b390ffe36ae1e911f4e3d5f7d0d725024e'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-9-runtime-executor-flags-ai-log-and-retention.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Security-sensitive actions leave no trace, and an administrator reading private data (an account's phone or email, a Private address) is not accountable for it.

**Approach:** Add a kernel audit writer that records an action in the caller's own transaction into an append-only table (the runtime database role can insert and read but not update or delete), validated against an allow-list of metadata schemas that owning apps register, plus a reasoned-read recorder `kernel.privacy.record_private_read(actor, reason, target_ref)` that every private admin read must call first and that fails closed, with a registry of private targets and a convention test that proves every admin private read uses it.

## Boundaries & Constraints

**Always:** the audit table `kernel_auditrecord` has a UUID primary key and plain UUID actor and target references (no foreign keys), action name, actor kind, target type, a `reason` column used only by reasoned reads, request id, allow-listed `metadata` JSON and creation time; the table is listed in `RUNTIME_APPEND_ONLY_TABLES` so the existing post-migrate grant gives the runtime role INSERT and SELECT only, and a test connects as the runtime role and shows INSERT and SELECT succeed and UPDATE, DELETE and TRUNCATE are refused by PostgreSQL; `audit.record(actor, action, target_type, target_id, metadata=None)` takes the actor first, raises `RuntimeError` unless called inside an open transaction (so the record commits or rolls back with the action), and writes through the caller's connection; owning apps declare actions with `register_action(action, schema)` where an action name is `domain.past_tense` (lowercase, one dot) and a schema maps each allowed metadata key to a type of `bool`, `int`, `uuid` or an enumeration of allowed strings, never free text; an unregistered action, an unknown or missing key or a wrong type is rejected before any write; a string value outside its enumeration, or any digit run of seven or more characters, is rejected so contact details and message text cannot be smuggled in; reasoned reads use a closed enumeration of private targets registered by owning apps with `register_private_target(name)` (duplicate name with the same arguments is a no-op, a conflicting redefinition errors); `record_private_read(actor, reason, target_ref)` requires an administrator actor (visitor `unauthenticated`, any other non-admin `not_found`), a registered target, a target id UUID and a reason of 10 to 300 characters after trimming, writes the audit record inside its own `transaction.atomic()` with the reason in the `reason` column, and fails closed: any write error propagates so the selector returns no data; the presentation layer provides a reusable POST-only request body serializer carrying the per-read reason; an AST convention test requires every function named `admin_read_*` in `apps/*/selectors.py` to call `record_private_read` as its first statement and is proven with failing fixtures (a function that reads first, a function that skips the recorder); a registry test proves every registered target is non-empty and well-formed; metadata and reasons never appear in the request log or error bodies; import-linter contracts, `openapi.yaml`, the authorisation matrix and existing convention tests keep passing; comments only where intent is not obvious.

**Never:** add real audit actions, real private targets or endpoints (the owning stories register their own), store free text or contact details in metadata, apply `kernel.privacy.patterns` (Story 1.14 owns the single contact regex and will validate reasons), add an audit viewer or admin API (later admin stories), schedule or run retention on the audit table (it is append-only and never purged by `run_retention`; the retention rule is decided later), or let the audit writer swallow its own errors.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Write in transaction | registered action inside `atomic`, valid metadata | one row, committed with the action | N/A |
| Rolled back | action fails after `record` | no audit row remains | N/A |
| Outside transaction | `record` called with no open transaction | refused | `RuntimeError` |
| Unregistered action | unknown action name | refused, nothing written | `ValueError` |
| Bad metadata | unknown key, missing key, wrong type, string outside enumeration | refused, nothing written | `ValueError` |
| Canary values | metadata carrying a phone, email, address or message text, or digit run of 7+ | refused, nothing written | `ValueError` |
| Bad action name | not `domain.past_tense` | refused at registration | error |
| Append-only | runtime role UPDATE, DELETE, TRUNCATE on the table | refused by PostgreSQL | permission denied |
| Runtime access | runtime role INSERT and SELECT | allowed | N/A |
| Private read, admin | admin actor, registered target, valid reason | audit row with reason, actor id, target; selector may then read | N/A |
| Private read, others | visitor, user or system actor | `unauthenticated` or `not_found`, nothing written | N/A |
| Unknown target | unregistered target name | refused | `ValueError` |
| Bad reason | shorter than 10 or longer than 300 characters, or blank | refused, nothing written | `ValueError` |
| Audit write fails | database error during the write | exception propagates, no data returned | fails closed |
| Convention | `admin_read_*` selector without `record_private_read` as first statement | convention test fails (failing fixtures) | N/A |
| Registration | duplicate identical action or target | no-op; conflicting schema errors | error |
| Request body | reasoned-read serializer | requires `reason`, explicit field allowlist | 400 envelope |

</frozen-after-approval>

## Code Map

- `apps/kernel/audit.py` -- `register_action`, `record`, registry; `apps/kernel/models.py`, `apps/kernel/migrations/0005_audit_record.py` -- `AuditRecord` with indexes on (target_type, target_id), actor_id and created_at
- `apps/kernel/privacy/__init__.py`, `apps/kernel/privacy/reads.py` -- `register_private_target`, `record_private_read`, target registry (the package leaves room for `privacy.patterns` in Story 1.14)
- `apps/presentation/api/private_reads.py` -- `ReasonedReadSerializer` (explicit `reason` field, length bounds) and a small POST-only mixin or base for later admin read views
- `config/settings/base.py` -- `RUNTIME_APPEND_ONLY_TABLES = ["kernel_auditrecord"]`; confirm the grant code in `config/privileges.py` and the post-migrate hook revoke UPDATE, DELETE and TRUNCATE
- `tests/audit/`, `tests/privacy/`, `tests/architecture/test_admin_read_convention.py`, `tests/test_db_roles.py` -- every matrix row, runtime-role proofs, failing fixtures
- `docs/audit.md`, `README.md` -- how an owning app registers actions and private targets, the in-transaction rule, the append-only rule, the adoption note that Stories 1.12 and 1.18 write the account-state and password-change audit records through this writer

## Tasks & Acceptance

**Execution:**
- [ ] model, migration, `RUNTIME_APPEND_ONLY_TABLES`, grants -- append-only audit table
- [x] `apps/kernel/audit.py` -- registry and in-transaction writer with metadata allow-list
- [x] `apps/kernel/privacy/`, `apps/presentation/api/private_reads.py` -- reasoned-read recorder, target registry, reason serializer
- [ ] tests and convention test with failing fixtures -- cover every matrix row
- [ ] docs and README

**Acceptance Criteria:**
- Given an action, when a service calls the audit writer, then the record is written in the same transaction with plain UUID references and an allow-listed metadata schema, and a rolled-back action leaves no record.
- Given the runtime database role, when it attempts UPDATE or DELETE on the audit table, then the database refuses, while INSERT and SELECT succeed.
- Given earlier and later stories, when passwords change or account state changes, then an audit record is written for each through this writer (adopted by Stories 1.12 and 1.18), and metadata never contains phone, email, address or message text (tested with canary values).
- Given the reasoned-read recorder, when an owning app serves a private read, then its `admin_read_*` selector first calls `record_private_read`, which writes the audit record and fails closed, with a POST-only request carrying a per-read reason and targets from a closed registered enumeration.
- Given the registered targets, when the enumeration test runs, then every `admin_read_*` selector is proven to call the recorder first and the test fails for one that does not.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | Int metadata bypasses the contact-detail guard (BH, EC) | high | patch | A phone number as an int would be stored in a never-purged table; ints with 7+ digit runs now refused. |
| 2 | `record(reason=...)` is an unguarded free-text channel; any app could write `privacy.viewed` (BH, EC, VG) | high | patch | Reason moved to a module-private writer; public `record` refuses the action. |
| 3 | Reasons unscreened and permanent; target-name length mismatch (BH, EC) | medium | patch | `@` and digit runs refused pending Story 1.14; name limits aligned. |
| 4 | No database invariants on AuditRecord (BH) | medium | patch | CheckConstraints on actor kind and actor id added. |
| 5 | Reasoned-read mixin untested; integer reason coerced; POST-only lost by bad ordering (VG, BH, EC) | medium | patch | Tests, string-only field and `__init_subclass__` guard. |
| 6 | Convention test narrow, vacuous, accepts look-alikes, rejects async (BH, EC) | medium | patch | Scans packages, resolves imports, checks argument count. |
| 7 | Request-id truncation untested; role probe leaves a committed row; unhashable enum TypeError (VG, EC) | low | patch | Fixed. |
| 8 | Flag changes are not audited (BH) | medium | defer | Needs a flag-name token schema; belongs to the admin stories. |
| 9 | Append-only only by role grants; trigger as defence in depth (BH) | low | reject | The story specifies the role refusal; residual migrator trust documented. |
| 10 | Non-default DB alias, process-global registries, no action index, created_at from app clock (BH, EC) | low | reject | Unlikely in everyday use; fix adds complexity. |

## Design Notes

Decisions made without asking: the per-read reason is a 10 to 300 character note kept in its own column rather than in the metadata allow-list, because an administrator must be able to explain a read in words, and Story 1.14's contact-pattern check will be applied to it; the registry-based allow-list keeps free text out by construction; the audit table is never purged by retention because it is append-only for the runtime role (a retention rule is decided with the moderation epic); the AST convention test, not a runtime registry of selectors, is what proves every `admin_read_*` selector uses the recorder, because selectors live in apps the kernel must not import; the account-state and password-change audit rows named in the epic are written by the stories that create those actions (1.12 and 1.18), through this writer.

## Verification

**Commands:**
- `uv run python manage.py migrate --database=migrator` -- applies migration 0005 and the append-only grant
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest && uv run python manage.py makemigrations --check --dry-run` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && git diff --exit-code openapi.yaml` -- expected: no diff
