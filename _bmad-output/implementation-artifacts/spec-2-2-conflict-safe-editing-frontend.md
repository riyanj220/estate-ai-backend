---
title: 'Story 2.2: Conflict-safe editing (frontend half)'
type: 'feature'
created: '2026-11-20'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-2-conflict-safe-editing.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-1-start-a-draft-frontend.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/registry.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The backend now saves a Draft's title, description and price with revision checking, but the Draft page is read-only: there is no form, no conflict handling, and nothing tells an owner that another session changed their Draft.

**Approach:** In the frontend repository (`/home/riyan/Riyan/projects/estate-ai-frontend`, branch `story/2-2-conflict-safe-editing`; the client is already synced) turn the Draft page into an editor for the fields introduced so far (title, description, Asking price, price basis and, for rent, rental period): the form is built from the registry, saves only what changed with the current `base_revision`, shows server validation per field, and on a `409` reloads the current data while keeping the owner's unsaved input so they can reapply it. The frontend decides nothing about validity beyond showing hints from the registry; the server judges every value.

## Boundaries & Constraints

**Always:**
- Same-origin typed client calls through `getApi` and the proxy; reuse `withTimeout`, `readErrorBody`, `rateLimitMessage`, `withCsrf` for the save, the registry hook (extend it to expose the field definitions: id, type, `min_length`, `max_length`, money `min`/`max`, `allowed_values_by_purpose`, applicability), the shared form pieces (`Field`, `ErrorSummary`, `useFormFeedback`), the draft loader and not-found handling from Story 2.1, the guard and `no-store`, and the accessibility conventions of the profile pages. No listing content, price or text goes to storage, console or URL (an unsaved-input guard may use `beforeunload` only, never storage).
- **Editor on the Draft page** (`/account/listings/[id]`): below the read-only summary of purpose, Category and Subtype, a form with Title (text input), Description (textarea), Asking price (text input with a PKR prefix label, `inputmode="decimal"`), Price basis (select) and Rental period (select, shown only when the purpose is rent). Labels, requirement hints ("Required before you publish" for fields the registry marks required for publication, "Recommended" for recommended) and character limits come from the registry. Price basis choices come from `allowed_values_by_purpose[purpose]` (sale: total, per unit; rent: rental period) and rental period choices from its `allowed_values`; changing nothing in the registry changes nothing in the frontend (a test proves a stub registry with different limits or values changes the form). A live plain-text counter for title and description (counted in code points after whitespace collapse, never announced on every keystroke) with an over-limit hint; no `maxlength` attribute that silently truncates a paste.
- **Saving**: an explicit "Save changes" button (no autosave); it sends `PATCH /api/v1/listings/{id}` through `withCsrf` with `{base_revision: <the revision of the data last loaded>, fields: {...}}` containing ONLY the fields the owner changed (an emptied field is `null`; nothing changed: no request and a polite "No changes to save."); price is sent as the exact text the owner typed, trimmed (no number conversion in the client, so no float rounding); the displayed price is formatted from the server's string by string manipulation only (group digits with commas, keep the two decimals, never `parseFloat`). `200` replaces the loaded listing (new revision shown, "Saved" in the single live region), resets the baseline so the form has no unsaved changes, and keeps focus where it was; the save button uses `aria-disabled` while saving and a double click sends exactly one request.
- **Validation errors** (`400`): map the field-level codes from the envelope to human text against the right control with the error summary focused and the typed values kept: `required`, `invalid`, `too_short`, `too_long`, `invalid_price`, `contains_contact_details` (the explanation text), `not_applicable`, `invalid_choice`, `unknown_field`/`not_editable` (generic); known codes only (unknown codes, prototype keys and unknown field keys become generic text, never server text verbatim); errors clear on edit of that field; the summary never links to a hidden or disabled control.
- **Conflict flow** (`409` with code `stale_revision` and a `current` listing): never overwrite anything silently. Replace the loaded listing and the baseline with `current` (new revision), keep every value the owner typed in the form exactly as typed (do not reset the inputs), show a conflict banner in the live region and in the page ("This draft was changed in another session. We loaded the latest version; your unsaved changes are still in the form. Review them and save again."), and for each field where the owner's typed value differs from the latest saved value show the latest value next to it ("Latest saved: ..." plain text, for the price formatted as above) with two small actions: "Keep mine" (leave as typed) and "Use latest" (replace the input with the latest value); the next save uses the new `base_revision` and sends only the fields that now differ from the latest data. A second `409` repeats the flow. A `409` whose body has no usable `current` falls back to reloading the listing through GET and keeps the typed input. A retried save after the first succeeded (lost response) is a `409` whose `current` already holds the values: the "differs" list is empty and the banner says "Your changes are already saved." when every typed value equals the latest.
- **Other answers**: `404` shows the Draft not-found state of Story 2.1 (identical copy for every cause); `403 forbidden` for a Suspended owner shows "Your account cannot edit listings right now." only when the session says the account is suspended, otherwise the generic rejected-request message (the real backend answers CSRF failures with the same `forbidden` code); `401` flips the provider to signed out and goes to sign-in with `next`; 429, 5xx, timeout and network failures keep the typed input and show their own generic messages with the save still available; a persistent failure never loses what the owner typed.
- **Unsaved changes**: a `beforeunload` guard while the form has unsaved changes (removed after a successful save); leaving through in-app links is not intercepted (documented).
- **bfcache and sessions**: reuse the pageshow re-check of Story 2.1 so a restored page after sign-out does not show the previous account's draft; the form values are not persisted anywhere.
- **Draft page header**: shows the status, the revision and the registry version as before.
- Tests (Playwright against the stub backend, which gains a stateful `PATCH /api/v1/listings/{id}` with the real revision rules: stale base returns `409` with `{error:{code:"stale_revision",...}, current}`, no-op returns the current listing, field validation modes (`invalid`, `too_short`, `too_long`, `invalid_price`, `contains_contact_details`, `not_applicable`, `invalid_choice`), a rent listing preset, a helper that bumps the stored revision and changes a field to simulate another session, and registry version 2 with the five new field definitions and `allowed_values_by_purpose`) cover every matrix row; axe at desktop and mobile on the editor states; existing suites keep passing; lint with `--max-warnings 0`, typecheck, build and `api:check` pass; README documents the editor, the conflict flow and the stub modes. IMPORTANT: gitleaks flags password-looking high-entropy literals: use only low-entropy fake-word values and repetitive digit patterns; assert on the element that owns a text (never page-wide `getByText`); no serial mode; settle before negative assertions (poll a stub counter), and keep Retry or Save controls `aria-disabled` rather than `disabled`.

**Never:** convert the price to a JavaScript number; truncate a paste with `maxlength`; send unchanged fields; overwrite the owner's typed input on a conflict or any failure; send `origin`, `confirmed`, `status` or any field the form does not own; show the other session's data for a 404/403; store any listing text or price in storage; show raw server text for unknown codes; poll on a timer; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Editor renders | Draft with some fields set, registry v2 | form prefilled; limits, hints and price-basis choices from the registry | N/A |
| Rent vs sale | purpose rent / sale | rental period control only for rent; price basis choices per purpose | N/A |
| Registry drives the form | stub registry with other limits or values | form and counters follow it | test |
| Save ok | changed title only | PATCH body `{base_revision, fields:{title}}`; revision updated; "Saved"; no unsaved state | N/A |
| Nothing changed | no edits | no request; "No changes to save." | N/A |
| Clear a field | description emptied | PATCH sends `description: null` | N/A |
| Price text | `1500000`, `1,500,000.50` | sent as typed text; shown formatted from the server string; never a float | test |
| Field errors | 400 codes incl. `contains_contact_details`, `invalid_price`, `not_applicable` | mapped text under the right controls, summary focus, values kept | N/A |
| Unknown code | unknown code or prototype key | generic text, never server text | N/A |
| Conflict | PATCH answers 409 with `current` | listing and baseline replaced; typed input kept; banner; per-field "Latest saved" with Keep mine / Use latest; next save uses the new revision | N/A |
| Conflict then save | after the flow | PATCH carries the new `base_revision` and only differing fields | N/A |
| Second conflict | another 409 | flow repeats without losing input | N/A |
| Lost-response retry | first save landed, retry is 409 | banner "Your changes are already saved" when typed equals latest | N/A |
| 409 without `current` | malformed body | reload through GET, keep typed input | N/A |
| Suspended / other 403 | 403 `forbidden` | suspended message only when the session says suspended, else generic | N/A |
| 404 | unknown, foreign, malformed id | Story 2.1 not-found copy | N/A |
| Session gone | 401 | signed out, redirect to sign-in with `next`; administrators to the admin sign-in | N/A |
| Failures | 429, 5xx, timeout, network | own generic messages, typed input kept, Save still available | N/A |
| CSRF self-heal | first PATCH 403 | token refetched, retried once | second 403 generic |
| Double save | click twice | exactly one request | N/A |
| Unsaved guard | edit then leave | `beforeunload` guard; removed after a save | N/A |
| bfcache | Back after sign-out | no previous account's draft shown | N/A |
| Privacy | after any state | no listing text, price or revision in storage, console, title or URL | test |
| Accessibility | editor, errors, conflict, saved states | axe clean at desktop and mobile; one live region announces once | test |

</frozen-after-approval>

## Code Map

- `src/lib/use-registry.tsx` (field definitions), `src/lib/listing-labels.ts` (price formatting by string), `src/lib/listing-messages.ts` (field codes to text)
- `src/app/(app)/account/listings/[id]/{draft-client,draft-editor}.tsx`
- `tests/e2e/stub-backend.mjs`, `tests/e2e/listing-edit.spec.ts`, README

## Tasks & Acceptance

**Execution:**
- [ ] registry field definitions in the hook; price formatting helper
- [ ] editor form on the Draft page with registry-driven choices and limits
- [ ] save with changed fields only, validation mapping, conflict flow, other answers
- [ ] stub PATCH with revision rules, Playwright tests, README

**Acceptance Criteria:**
- Given a Draft I own, when I edit title, description, price, price basis and (for rent) rental period and save, then the changes are saved against the current revision and I see validation errors per field.
- Given another session changed my Draft, when I save, then I see the current data with the option to reload, and my unsaved input is preserved so I can reapply it.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Three reviewers (blind, edge-case, verification-gap). Patched in one batch: commas in prices (the real backend rejects them; stub made strict, priceKey no longer strips them, clearer message, plain-digit hint); revision safety (200 or 409 `current` needs same id and an integer revision, 409 must be newer, else reload by GET); text comparison now NFC + whitespace collapse like the server; "already saved" only when nothing else moved; conflict banner clears after every row is resolved; dead helper removed; seven added tests.
Deferred: in-app navigation guard (needs storage or a router interceptor; accepted limit, documented), keeping typed input across a bfcache restore (restore remounts from server data), admin-401 test (administrators get 404 on Drafts), `not_editable` has its own text instead of generic, timeout copy does not say a save may have landed, stub gaps (punctuation-only text, negative price code).
Verification: lint, typecheck, build, api:check, full Playwright 833 passed.

## Design Notes

Decisions made without asking: the editor saves explicitly (no autosave) so a conflict can only arise from a deliberate action and the owner always sees the result; only changed fields are sent so a conflict on an untouched field never blocks an edit; the conflict flow keeps the typed input and shows the latest value beside it, which is the least destructive resolution; money stays text in the client so no float rounding can alter a price; the form is built from the registry so later stories add fields by registry entry plus a small editor mapping.

## Verification

**Commands (frontend repository):**
- `npm run api:sync && npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` -- expected: all pass (the full Playwright run takes about 20 minutes: run it in the background with output to a file; never run two Playwright runs at once)
