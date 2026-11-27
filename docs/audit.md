# Audit trail and reasoned private reads

## Writing an audit record

Declare each action once from your app's `AppConfig.ready`. An action name is `domain.past_tense`
(lowercase, one dot). The schema maps every allowed metadata key to `bool`, `int`, `uuid.UUID` or a
collection of allowed strings. Never free text.

```python
from apps.kernel import audit

audit.register_action("account.suspended", {"reason_code": ["abuse", "fraud"], "was_active": bool})

with transaction.atomic():
    account.suspend()
    audit.record(actor, "account.suspended", "account", account.id,
                 {"reason_code": "abuse", "was_active": True})
```

Rules:

- `record` takes the actor first and raises `RuntimeError` outside an open transaction, so the record
  commits or rolls back with the action. It never swallows errors.
- All schema keys are required; unknown keys, wrong types, strings outside the enumeration and any
  string with a run of seven or more digits raise `ValueError` before anything is written.
  Errors never echo values. Contact details and message text must not reach metadata.
- Registering the same action twice with the same schema is a no-op; a different schema errors.
- `kernel_auditrecord` is listed in `RUNTIME_APPEND_ONLY_TABLES`: the runtime role has INSERT and
  SELECT only; PostgreSQL refuses UPDATE, DELETE and TRUNCATE. `run_retention` never touches it;
  the retention rule is decided later.
- Append-only rests on role grants: the runtime role cannot UPDATE, DELETE or TRUNCATE. The
  migrator and superuser roles remain trusted and can change the table. The database also enforces
  that `actor_kind` is one of visitor, user, admin, system, and that `actor_id` is null exactly for
  visitor and system actors.
- Integer metadata values with a run of seven or more digits are refused like strings.
- `record` refuses the `privacy.viewed` action and has no reason parameter; only the recorder in
  `kernel.privacy.reads` writes a reason.
- Stories 1.12 (account state changes) and 1.18 (password change) write their audit records through
  this writer and register their own actions.

## Reasoned private reads

An administrator reading private data (an account's phone or email, a Private address) must explain
the read. Register each private target from `AppConfig.ready`:

```python
from apps.kernel.privacy.reads import register_private_target

register_private_target("account_phone", "An account's phone number")
```

The owning app's selector, named `admin_read_*` in `apps/<app>/selectors.py`, calls the recorder
as its first statement:

```python
def admin_read_account_phone(actor, reason, account_id):
    record_private_read(actor, reason, ("account_phone", account_id))
    ...
```

`record_private_read(actor, reason, target_ref)` requires an administrator (visitor gets
`unauthenticated`, other actors `not_found`), a registered target, a UUID target id and a reason of
10 to 300 characters after trimming. It writes the record (action `privacy.viewed`, reason in the
`reason` column) in its own `transaction.atomic()` and fails closed: a write error propagates, so
the selector returns nothing.

The view should use `apps.presentation.api.private_reads.ReasonedReadMixin` (POST only) and
`ReasonedReadSerializer`, so the reason travels in the request body, never a URL or the request log.

`tests/architecture/test_admin_read_convention.py` proves by AST that every `admin_read_*` function
in `apps/*/selectors.py` has `record_private_read` as its first statement (a docstring may precede
it). Interim reason rule: a reason containing `@` or a run of seven or more digits is refused with
`ValueError` (the same digit-run rule as metadata); Story 1.14's shared contact patterns replace
this. Target names follow the same pattern as audit target types (lowercase snake_case, at most 48
characters). Reason text is stored permanently, so never put contact details in it.

Registered targets: `account` (Story 2.21, the administrator account lookup: `identity.selectors.admin_read_account`),
`verification_request` (Story 8.4), `listing_draft` (Story 9.3, `listings.selectors.admin_read_draft_listing`) and
`report_snapshot` (Story 9.5, `governance.selectors.admin_read_conversation_report`).
Story 10.4 adds `account_contact` (email, phone, office address; `identity.selectors.admin_read_account_contact`) and
`listing_address` (one Listing's Private address; `listings.selectors.admin_read_listing_address`). Administrators review
the trail at `GET /api/v1/admin/audit-records` (Story 10.7), read only.
The lookup is by email, so `account_id_for_admin_lookup` first resolves the id (and returns nothing else); an unknown or
administrator address is `not_found` and writes no record. `ReasonedReadSerializer` also refuses a reason with line breaks
or contact details (`400`).
