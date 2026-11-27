# Stories 5.5 to 5.7: knowledge base index, content and grounded retrieval

Sources: `epics.md` Epic 5, FR-28, FR-78, AD-8, AD-11, SM-11. Backend only, no endpoint, OpenAPI unchanged.

## Departures from the architecture (owner delegated)

1. **Embeddings are not local (AD-8).** `gemini-embedding-001` (768 dimensions) through the existing embeddings port, instead of a vendored fastembed model. No model files, no new dependency. Consequences: indexing and every question call the provider, count against the daily budget, send the redacted question out, and retrieval is unavailable when the provider is. The default and the tests use the recorded stub (offline hashed bag-of-words vectors).
2. **No pgvector column (AD-11, spine).** Vectors are a float array on `catalog_knowledgechunk`, compared by exact cosine similarity with numpy. About 114 rows need no index; there is no new dependency and a model change needs a rebuild, not a migration. Each chunk records `provider:model`; vectors of another model are never used.

## Built (`apps/catalog`)

- **Content (5.6):** `knowledge_data/documents/*.yaml`, 8 documents, 114 passages, each document with title, version and provenance. `knowledge.lock` and `scripts/knowledge_lock.py` force a higher version on change.
- **Index (5.5):** tables `KnowledgeDocument`, `KnowledgeChunk` (migration `0007`); `services.rebuild_knowledge_index(actor)` (system or administrator), command `rebuild_knowledge_index [--pause S]`. Batches of 50 through `gateway.embed` (flag, budget, log); the index is replaced in one transaction, or not at all.
- **Retrieval (5.7):** `selectors.retrieve_knowledge(actor, question)` returns `found` (at most 3 passages with source, version, provenance, score), `not_found` (below the threshold) or `unavailable` (no index for the model, flag, budget, provider or database fault), plus a no-verification notice. It never raises for a fault; only a question over 2,000 characters raises.
- **Measure:** `knowledge_data/questions.yaml` (40 labelled, 5 off-topic), command `evaluate_knowledge` (one embedding request).
- `gateway.embed(..., reserved=True)` lets signed-in retrieval use the Listing creation budget; `gateway.embedding_model_key()` is new.

## Rules for Story 5.8

Passage text is untrusted data: pass it only through `LLMRequest.context` (redacted), never `system`. Never present it as verification of approvals, titles or possession. Call retrieval outside heavy executor tasks (it uses a slot).

## Operating

```sh
env -u DJANGO_SETTINGS_MODULE uv run python manage.py migrate --database=migrator
env -u DJANGO_SETTINGS_MODULE uv run python manage.py rebuild_knowledge_index            # stub
AI_PROVIDER=gemini env -u DJANGO_SETTINGS_MODULE uv run python manage.py rebuild_knowledge_index --pause 35
AI_PROVIDER=gemini env -u DJANGO_SETTINGS_MODULE uv run python manage.py evaluate_knowledge
```

Rebuild after changing the content, the provider or the embedding model: there is one index, for one model.

## Verification and open points

- Gates: ruff, format, lint-imports, `makemigrations --check`, spectacular validate (OpenAPI unchanged); full suite 6084 passed, 2 skipped.
- One real run, 2026-11-27, on the dev database: 114 passages indexed with `gemini:gemini-embedding-001` in 3 batched requests (`--pause 35`).
- Stub baseline: 37 of 40 in the top 3 (word overlap only; it is **not** SM-11).
- **SM-11 on Gemini is not measured yet**: run `evaluate_knowledge`.
- **The Gemini threshold 0.65 is provisional**; calibrate it from the scores `evaluate_knowledge` prints. Stub threshold 0.34.
- **The passages need the owner's review** (FR-78 asks for authored or reviewed content; they were AI-drafted).
- "The assistant still completes a Listing" is shown only at service level (retrieval faults, then manual Draft editing); the assistant turn itself is Story 5.8 and 5.15.
