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
- City, locality, price basis, rental period and other fields the registry does not mark `ai_extractable` are asked for in the form (city and locality: see the change below).
- Follow-up planning is code, not the model: computed from the Draft, so manual entries count. Required fields first (chat-fillable before form-only), then review with up to three optional suggestions.
- The pending question is sent as the last history entry, so a short answer has a context. No Listing value goes into the prompt.
- Plain prose from the model (no JSON) is a reply with no facts; broken JSON is an `error` outcome with nothing stored.

## Change after real-provider use: city and locality from chat

With real Gemini, a locality such as "Gulshan-e-Iqbal" was saved as `society` text while city and locality stayed empty. This departs from the decision above for these two fields only. The registry is unchanged: they are still not `ai_extractable`, so the model's schema never holds them.

- Code, not the model (`assistant/places.py`), matches the redacted message and the model's `society`, `phase`, `sector` and `block` texts against the active catalogue localities. No extra model call.
- Whole names only, ignoring case, spaces, hyphens and the connecting "e"/"i"; "Johar" reads as "Jauhar" and "Defence Phase" as "DHA Phase". The longest name wins. Two different localities mean no guess.
- In the message, "Garden" and "SITE" are never matched, nor is a name after "near", "from", "opposite", "behind" or "towards", or before "road", "ke qareeb" or "ke paas".
- One match sets city and locality through `apply_updates` with origin `ai`: unconfirmed, so publication needs the Seller's confirmation (both are quota identity fields). The turn's `applied` reports their names.
- A value the Seller typed or confirmed is never overwritten. A manual city limits the match to that city. An earlier unconfirmed AI value follows a correction.
- A `society` that only repeats the matched locality name is dropped.
- The review question now also names the city when it is unconfirmed.

## For later stories

- 5.11 adds nearby facilities to the turn schema; `clarify` already carries clarifying questions.
- 5.12 confirms the unconfirmed values (publication today refuses only identity and private ones; asking price is for 5.12).
- 5.13 generates the description. 5.14 replays the stored turn result. 5.15 relies on the shared transaction.

## Checks

`tests/assistant/test_extraction.py`, egress entry `assistant-extraction`, frontend `tests/e2e/assistant-extraction.spec.ts` (axe). Real-provider extraction accuracy is not measured.
