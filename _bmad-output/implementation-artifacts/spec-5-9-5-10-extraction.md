# Stories 5.9 and 5.10: extraction, applying updates and follow-up questions

Sources: `epics.md` Epic 5, FR-25, AD-4, AD-8, AD-10. Backend and frontend. Builds on spec-5-8.

## API (additions to spec-5-8)

- Turn: `applied[{field, value}]`, `rejected[{field, code}]`, `conflicts[field]`, `notices[]`.
- Session and turn result: `next_step {kind: required|review, question, fields_asked[], optional[], in_form}` or null.

## Decisions

- One model call per turn: a strict schema `{reply, clarify, fields}`, `fields` being the registry's extraction schema for the Draft's purpose and Subtype. `description` is left out (5.13). Schema version 2.
- Only schema fields are read. Writes go only through `apply_updates` with origin `ai`, so values are stored unconfirmed. `apply_updates` refuses origin `ai` on anything but a Draft.
- A failing value is dropped and reported; the rest are saved. Area value, unit and marla basis are saved together or not at all.
- The write uses the revision read before the model call. On `stale_revision` it reconciles once: fields changed meanwhile are left alone and reported as conflicts.
- The Draft write and the stored turn share one transaction.
- A text value that redaction would change is refused. A message that redaction changed adds the "use the form" notice.
- City, locality, price basis, rental period and other fields the registry does not mark `ai_extractable` are asked for in the form.
- Follow-up planning is code, not the model: computed from the Draft, so manual entries count. Required fields first (chat-fillable before form-only), then review with up to three optional suggestions.
- The pending question is sent as the last history entry, so a short answer has a context. No Listing value goes into the prompt.
- Plain prose from the model (no JSON) is a reply with no facts; broken JSON is an `error` outcome with nothing stored.

## For later stories

- 5.11 adds nearby facilities to the turn schema; `clarify` already carries clarifying questions.
- 5.12 confirms the unconfirmed values (publication today refuses only identity and private ones; asking price is for 5.12).
- 5.13 generates the description. 5.14 replays the stored turn result. 5.15 relies on the shared transaction.

## Checks

`tests/assistant/test_extraction.py`, egress entry `assistant-extraction`, frontend `tests/e2e/assistant-extraction.spec.ts` (axe). Real-provider extraction accuracy is not measured.
