# Story 5.4: provider adapters and availability trial

Sources: `epics.md` Story 5.4, AD-8, spec-5-1-to-5-3. Backend only; no endpoint, no migration.

## Built

- `kernel/ai/transport.py`: HTTPS JSON with `http.client` (no redirects, no proxy variables), `check_url`, `ProviderError` with category and status only.
- `kernel/ai/gemini.py`: LLM with `responseJsonSchema` and one retry on a 400, batched embeddings, speech-to-text from inline audio. `AI_PROVIDER=gemini` selects it; the stub stays the default.
- `catalog/extraction.py`: strict-mode schema per purpose and Subtype from the registry's `ai_extractable` fields.
- `manage.py ai_provider_trial`; results in `docs/ai-providers.md`.
- Gateway: embeddings from a provider (not the stub) count against the open part of the daily budget and log the embedding model.
- Tests: `tests/ai/test_gemini.py` on a fake connection; an autouse fixture refuses any real provider connection, and test settings drop the key.

## Decisions

- No Groq adapter: no key, so it could not be tried. The first two acceptance criteria are met for Gemini only (deferred).
- No SDK and no LangChain.
- Default model `gemini-3.5-flash-lite`, on the trial's latency evidence.
- The trial is a `catalog` command because it needs the registry, which `kernel` may not import. It calls the adapter directly, outside the gateway's budget and log.
- AD-8 says the embedding model runs locally. The Gemini embeddings port exists, but Story 5.5 still decides which model builds the index.

## Left for later

- Free-tier request limits are unverified; set `AI_DAILY_BUDGET` from AI Studio.
- Unpaid-tier terms forbid personal data; decide on the paid tier before real users.
- Browser audio formats are untested; the voice-search endpoint must check the content type.
