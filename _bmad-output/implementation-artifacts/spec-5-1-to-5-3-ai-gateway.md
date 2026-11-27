# Stories 5.1 to 5.3: AI gateway, controls and external-service notice

Sources: `epics.md` Epic 5, AD-8, AD-25, FR-32. 5.1 and 5.2 are backend, 5.3 is frontend. No endpoint, so the OpenAPI contract is unchanged.

## Built

**5.1 (`apps/kernel/ai/`)**
- `redact(text, known_values)` is the only constructor of `RedactedText`: exact known phone, email and address first (`find_known_values` in `privacy/patterns.py`, so every contact regex stays in one module), then the shared patterns.
- Known values come from sources registered by the owning apps (identity: emails, phones, office address; listings: private addresses).
- Ports `LLMPort`, `EmbeddingsPort`, `SpeechPort`; the recorded stub implements all three and is the only adapter (`AI_PROVIDER=stub`).
- Boundary: `egress.ALLOWED_HOSTS` and `check_url`; an import contract for the domain apps and presentation; an AST test for everything outside `kernel/ai`.
- Egress canary harness `tests/privacy/test_ai_egress.py`: later entry points add a function to `ENTRY_POINTS`.

**5.2**
- `gateway.complete / transcribe / embed`: input cap, `ai_gateway` flag, per-account or per-Visitor limit (existing policy rows), daily budget, timeout on the runtime executor, request log.
- Ledger `kernel_aibudgetledger` (provider, UTC day), settings `AI_DAILY_BUDGET` (1000) and `AI_RESERVED_PERCENT` (50 to 100), retention 90 days.

**5.3 (frontend)**
- `useAiNotice()` in `src/components/ai-notice.tsx`; first user is a "Create with AI" entry on Create listing, shown only with `AI_FEATURES=1`.

## Decisions

- A refused call returns an `AiResult` fallback with a message; only misuse raises (plain string, input over the cap as `validation_error`, Visitor on a signed-in feature).
- The cap counts the user's text before redaction, not instructions, history or passages.
- The budget counts requests, not tokens, at admission. Only the signed-in `assistant_turn` uses the reserved part. A ledger fault fails closed.
- Embeddings are local: flagged and logged, no limit or budget. A voice search counts against the search limit once for transcription and again for the query it becomes.
- Audio cannot be redacted and goes with no account data; a transcript must be redacted before it is sent on or stored.
- Notice: "Continue" remembers and starts; "Not now" and Escape start nothing and remember nothing, so it returns. Declining must not count as having been told and agreed.

## Left for later stories

- 5.4: real adapters (own HTTP timeout, no redirects, `check_url`, no provider body in exceptions, one retry on 400, strict-mode schemas).
- 5.8: the gateway blocks its thread up to the timeout; the assistant endpoint should be async (`sync_to_async`) and must not call it from a heavy executor task. Add its egress case and replace the placeholder entry.
- 5.13 declares its own `Feature`.
