# AI providers and availability trial (Story 5.4)

## Choosing the provider

| Variable | Meaning |
|---|---|
| `AI_PROVIDER` | `stub` (default, no network) or `gemini`. Tests always use `stub`. |
| `GEMINI_API_KEY` | Secret, in the git-ignored `.env` or the process environment. Required for `gemini`. |
| `GEMINI_MODEL` | LLM and speech-to-text model. Default `gemini-3.5-flash-lite`. |
| `GEMINI_EMBEDDING_MODEL` | Default `gemini-embedding-001` (768 dimensions requested). |

The key is sent only in the `x-goog-api-key` header to `generativelanguage.googleapis.com`.
Provider errors are reduced to a category and status code.

**Primary: Gemini `gemini-3.5-flash-lite`.** Second LLM: `gemini-3.5-flash` through `GEMINI_MODEL`.
There is no Groq adapter yet (no key), so no cross-provider comparison or fallback.

## Running the trial

It makes real calls with invented text only, and stops on a rejected key or a quota refusal.

```sh
ffmpeg -f lavfi -i "flite=text='I am looking for a two bedroom apartment for rent':voice=slt" -ar 16000 -ac 1 /tmp/sample.wav
env -u DJANGO_SETTINGS_MODULE uv run python manage.py ai_provider_trial --audio /tmp/sample.wav
```

A full run is 7 calls. `--skip-list` omits the model list; `--samples N` limits the extractions.

## Results, 2026-11-27 (15 calls in total)

The key is accepted. No quota refusal was seen.

| | `gemini-3.5-flash-lite` | `gemini-3.5-flash` | `gemini-2.5-flash` |
|---|---|---|---|
| Available | yes | yes | listed, but `generateContent` returned 404 |
| Structured output conforming to the registry schema | 4/4 | 4/4 | not tested |
| Expected facts found | 12/13 | 12/13 | |
| Extraction latency | 1.4 to 2.4 s | 2.0 to 5.7 s | |
| Speech-to-text (one short clip) | correct, 1.5 s | correct, 21.3 s | |

- Embeddings (`gemini-embedding-001`): two texts, 768 dimensions, about 0.7 s.
- Schemas were 2.3 to 4.7 KB, far below the input limit.
- `gemini-3.5-flash` took longer on speech than the 12 s voice-search timeout.
- Also listed for this key: `gemini-flash-latest`, `gemini-flash-lite-latest`, `gemini-3.1-flash-lite`,
  `gemini-3.6-flash` to `gemini-3.8-flash`, `gemini-3.5-transcribe`, `gemini-embedding-2`. None was tried.
- Listed token limits for both tested LLMs: 1,048,576 input, 65,536 output.

This is a small sample: one run, synthetic English and one Roman Urdu sentence, one machine-voiced
WAV clip. It is not an accuracy evaluation (Story 11.5), and browser audio formats (WebM) are untested.

## Free-tier limits

**The numbers could not be determined.** The API's model list has token limits only, and the public
[rate limits page](https://ai.google.dev/gemini-api/docs/rate-limits) publishes no free-tier figures
for these models (read 2026-11-27, after a redirect to `aistudio.google.com`). It says:

- limits are per project, on requests per minute, input tokens per minute and requests per day;
- the daily quota resets at midnight Pacific time; going over returns 429;
- the project's actual limits are shown at <https://aistudio.google.com/rate-limit>.

To do: read the project's limits there and set `AI_DAILY_BUDGET` to the requests-per-day figure.
The default 1000 is not verified. The budget day is UTC, not Pacific, and counts a retried call once.

## Terms: retention and training use

From the [Gemini API Additional Terms](https://ai.google.dev/gemini-api/terms) (effective 2026-03-23,
read 2026-11-27), for unpaid use:

- Google uses prompts and responses to provide and improve its products and models.
- Human reviewers may read them (disconnected from the account and key first).
- No general retention period is stated.
- The terms say not to submit sensitive, confidential or personal information.
- Apps offered to users in the EEA, Switzerland or the UK must use paid services.

**Control: redaction.** Ports accept only `RedactedText` (AD-8). It is best effort for free-text
addresses and names, and audio cannot be redacted at all. So the free tier suits development with
synthetic data. Before real users' text or voice is sent, the owner should decide whether to move to
the paid tier.
