# Story 5.8: assistant turn endpoint, chat shell and retrieval integration

Sources: `epics.md` Epic 5, FR-24, FR-28, AD-8. Backend and frontend.

## API

All signed-in, no-store; POST needs `X-CSRFToken`.

| Route | Body | Answer |
| --- | --- | --- |
| `POST /api/v1/assistant/sessions` | `{listing_id}` | `201` new or `200` open session |
| `GET /api/v1/assistant/sessions/{session_id}` | | `200` session |
| `POST /api/v1/assistant/sessions/{session_id}/turns` | `{message, client_turn_id}` | `200` `{outcome, message, turn}` |

- Session: `{id, listing_id, registry_version, schema_version, created_at, knowledge_notice, turns[]}`.
- Turn: `{id, sequence, client_turn_id, message, reply, sources[{title, source, version}], created_at}`.
- `outcome`: `success`, `fallback`, `error`, `timeout`, `budget_exhausted`. Only `success` has a `turn`.
- Refusals: 401 Visitor; 404 another account's session or Listing; 403 Suspended or Deactivated; 409 not a Draft, a message already in flight, or 200 turns; 400 `message` (1 to 2,000) or `client_turn_id`; 422 a `client_turn_id` repeated with another message; 429 over 30 messages an hour.

"Create with AI" creates the Draft with the existing `POST /api/v1/listings`, then opens its session.

## Decisions

- Tables `assistant_assistantsession` (one open per Listing; registry and extraction schema versions; `closed_at` unused so far) and `assistant_assistantturn` (redacted `user_text` and `reply_text`, `client_turn_id`, `sources`).
- A turn is claimed (empty reply) before the model call and deleted if it fails, so a failed message leaves no history and can be sent again with the same id. A claim older than 90 s is discarded.
- A repeat of an answered `client_turn_id` returns it without a model call (the base for 5.14).
- Free tier: one model call per turn. A message that looks like a question also embeds once for retrieval; others do not. Nothing is spent once the message limit is reached.
- The prompt is a code constant. History (last 6 turns) and passages are data; passages go only in `LLMRequest.context`. No Listing text or field is read into the prompt.
- The reply is untrusted: control characters stripped, redacted, capped at 4,000, rendered as plain text.
- The assistant changes nothing in the Listing yet and says so (extraction is 5.9, which adds fields to the turn result).
- History is removed on `listing.deleted`; the retention job `assistant.history` repairs what that misses.
- Frontend: `/account/listings/{id}/assistant`, behind `AI_FEATURES=1`, with the external-service notice and a link to the Draft's manual form.

## Acceptance and checks

`tests/assistant/test_turns.py`, egress entry `assistant-turn` in `tests/privacy/test_ai_egress.py`, frontend `tests/e2e/assistant-chat.spec.ts` (axe).

## Open

- Turn p90 within 15 s is **not measured** against Gemini. Worst case is bounded by 12 s retrieval plus 20 s model.
- Question detection is a keyword rule (English and a few Roman Urdu words).
