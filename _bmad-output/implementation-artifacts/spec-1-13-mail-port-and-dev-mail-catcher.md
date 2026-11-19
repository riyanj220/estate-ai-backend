---
title: 'Story 1.13: Mail port and dev mail catcher'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '20deda053f96a29e3a78058ba989d292e712b6f2'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-12-account-model-states-and-capability-matrix.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Verification, reset and decision emails have no sending path, so none of those flows can be built or tested, and nothing guarantees an email is sent only for work that actually committed.

**Approach:** Add one kernel mail port. A feature calls `mail.send(message)` inside its transaction; the port emits a `mail.requested` domain event that is delivered only after commit by a kernel subscriber, off the request thread, through Django's email backend (the Mailpit catcher locally, an in-memory backend in tests, SMTP from the environment in production). Links in emails are built from `FRONTEND_BASE_URL`.

## Boundaries & Constraints

**Always:** the port lives in `apps/kernel/mail.py`; `MailMessage` is a frozen dataclass with one recipient address, a subject, a plain-text body and an optional HTML body; recipient, subject and sender are validated against header injection (no CR, LF or other control characters; recipient shape check; subject at most 200 characters; body at most 100 000 characters); `send(message)` requires an open transaction (`RuntimeError` otherwise) and does nothing but emit `MailRequested` (event name `mail.requested`) so a rolled-back transaction sends nothing; the kernel registers its own subscriber in `AppConfig.ready` (through `apps.py`, matching the subscribe-location rule), which submits delivery to the runtime executor's interactive lane so SMTP latency never blocks the request thread and mail is never dropped for heavy-lane saturation; setting `MAIL_SEND_INLINE` (true in test settings, false elsewhere) delivers synchronously for tests; delivery uses Django's `EmailMultiAlternatives` and `EMAIL_BACKEND`; a delivery failure (including the backend raising) is logged with the exception class only, never the address, subject or body, never changes the response and is not retried (documented; verification and reset flows let the user request a new link); local settings use the SMTP backend pointed at the Mailpit catcher and refuse to start unless `MAIL_HOST` is a loopback address (`127.0.0.1`, `localhost` or `::1`) so local mail can never leave the machine; test settings use the in-memory backend; production settings use the SMTP backend configured from `MAIL_HOST`, `MAIL_PORT`, `MAIL_USER`, `MAIL_PASSWORD` and `MAIL_USE_TLS`, require `MAIL_HOST` and `MAIL_FROM`, and treat `MAIL_PASSWORD` as a secret that is never logged (existing prod-settings tests and the CI environment are updated for the new required variables); the sender address comes from `MAIL_FROM` (a local default is allowed); a link helper `email_link(path, params=None)` builds an absolute URL from `FRONTEND_BASE_URL` through the existing `frontend_url` (path rules and rejection of hostile paths stay), appends query parameters URL-encoded in sorted order, and never uses the Host header; no recipient address, subject, body or link is ever written to logs, audit metadata or error bodies; the Mailpit container stays on 127.0.0.1 only and the README explains how to view captured mail; import-linter contracts, convention tests, `openapi.yaml` and the authorisation matrix keep passing; comments only where intent is not obvious.

**Never:** write real email templates or copy (each flow's story owns its message), build a retry queue, outbox or bounce handling, add notifications (Story 1.24), add endpoints, log or store message content, deliver mail externally from local or test settings, or add a third-party mail provider package.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Send after commit | `send` inside a transaction that commits | message delivered once, after commit | N/A |
| Rolled back | `send` then the transaction rolls back | nothing delivered | N/A |
| Outside transaction | `send` with no open transaction | refused | `RuntimeError` |
| Header injection | CR, LF or control characters in recipient or subject | refused, nothing queued | `ValueError` |
| Bad recipient | empty, no `@`, multiple addresses, too long | refused | `ValueError` |
| Limits | subject over 200 or body over 100 000 characters | refused | `ValueError` |
| Delivery failure | backend raises | logged by exception class only; request response unchanged; later sends unaffected | N/A |
| Privacy | log output after a failure and after a success | no address, subject, body or link in any log record | canary test |
| Off the request thread | non-inline mode | delivery runs on the executor; `send`'s caller returns immediately | N/A |
| Inline mode | test settings | delivered synchronously | N/A |
| Local catcher | local settings and Mailpit | message appears in Mailpit's API and web UI on 127.0.0.1 | manual check recorded |
| Local safety | `MAIL_HOST` set to a non-loopback address in local settings | start-up fails naming the variable | `ImproperlyConfigured` |
| Prod config | `MAIL_HOST` or `MAIL_FROM` missing | start-up fails naming the variable | `ImproperlyConfigured` |
| Link | `email_link("/verify-email", {"token": "t"})` with a hostile Host header present | `FRONTEND_BASE_URL/verify-email?token=t` | hostile path rejected |
| Link params | parameter values with reserved characters | URL-encoded | N/A |
| Event bus | emit on rollback or failure | existing guarantees hold (failing subscriber isolated) | N/A |

</frozen-after-approval>

## Code Map

- `apps/kernel/mail.py` -- `MailMessage`, validation, `send`, `MailRequested` event, `deliver` (the subscriber body), `email_link`
- `apps/kernel/apps.py` -- `ready()` subscribes the delivery handler; `apps/kernel/links.py` -- reuse `frontend_url`
- `config/settings/base.py`, `local.py`, `test.py`, `prod.py` -- `EMAIL_BACKEND`, host, port, credentials, `DEFAULT_FROM_EMAIL`, `MAIL_SEND_INLINE`, loopback guard in local, required values in prod; `config/env.py` -- a small loopback validator if needed
- `.env.example`, `.github/workflows/ci.yml`, `tests/test_prod_settings.py` -- new variables
- `tests/mail/` -- every matrix row using the in-memory backend and the event bus fixtures; `scripts/` or README -- a manual check that sends one message through local settings and finds it via Mailpit's HTTP API (record the outcome in the story, run it if the container is up)
- `docs/mail.md`, `README.md` -- the port, how a feature sends mail, links, the no-retry rule, viewing Mailpit

## Tasks & Acceptance

**Execution:**
- [x] `apps/kernel/mail.py`, `apps.py` -- port, event, delivery subscriber, link helper
- [x] settings per environment, `.env.example`, CI, prod-settings tests -- backends and guards
- [x] `tests/mail/` -- cover every matrix row
- [x] manual Mailpit check, docs and README

**Acceptance Criteria:**
- Given the local environment, when the system sends a transactional email through the kernel mail port, then it is captured by the dev mail catcher and viewable locally, never delivered externally.
- Given a feature sending an email, when its transaction commits, then the email is sent after commit through the event bus, a rolled-back action sends nothing, and a send failure is logged without changing the response.
- Given a link in an email, when it is built, then it uses `FRONTEND_BASE_URL`.

## Implementation Notes

Manual Mailpit check run 2026-11-19 (`scripts/mail_check.py`, local settings, container up): message sent through the port appeared in `http://127.0.0.1:8025/api/v1/messages`. Result: OK.

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | `events.clear()` silently drops all mail; `ready()` twice would crash (EC claim, implementer) | high | patch | Self-healing idempotent subscription, called from `send`; the first version called `subscribe` from `mail.py` and the subscribe-location convention test caught it, so it moved to `subscribers.py`. |
| 2 | No local SMTP timeout; prod `MAIL_FROM` unchecked at boot; no implicit-TLS option; plaintext with credentials allowed; port range (BH, EC, VG) | medium | patch | Timeouts, boot-time sender validation, `MAIL_USE_SSL`, refusals and pinned defaults added. |
| 3 | Bracketed IPv6 loopback host returned with brackets (BH, EC) | low | patch | Normalised. |
| 4 | Recipient domain rules, format characters, empty body, non-string link parameters (BH, EC, VG) | medium | patch | Rejected with tests. |
| 5 | Assertion-free failure test (BH, VG) | low | patch | Rewritten to run the on-commit callback. |
| 6 | Manual check script hangs or leaves threads and a message (BH, EC) | low | patch | try/finally, timeout, cleanup. |
| 7 | Docs: lost queued mail, unescaped HTML, synchronous ValueError, abuse limits; unquoted MAIL_FROM (BH, EC) | low | patch | Documented and quoted. |
| 8 | Loopback host resolved through DNS, non-default DB alias, futures cancelled at shutdown logged (EC) | low | reject | Unlikely in everyday use; fix adds complexity. |
| 9 | Interactive-lane latency under saturation unbounded (EC claim) | low | reject | Documented caveat. |

## Design Notes

Decisions made without asking: delivery goes through the executor's interactive lane because AD-14 forbids holding the request thread for slow I/O and a verification email must not be dropped under AI load; failures are not retried because the flows that send mail all offer a resend, and an outbox is out of scope for a prototype; `MAIL_SEND_INLINE` exists only so tests can assert synchronously; local settings enforce a loopback mail host so the "never delivered externally" guarantee is checked by start-up rather than convention; the port carries no template engine because each flow's story owns its copy.

## Verification

**Commands:**
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest && uv run python manage.py makemigrations --check --dry-run` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && git diff --exit-code openapi.yaml` -- expected: no diff
- Manual: with `docker compose up -d`, send one message through the port under local settings and read it back from `http://127.0.0.1:8025/api/v1/messages`
