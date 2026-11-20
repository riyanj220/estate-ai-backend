---
title: 'Story 2.1: Start a Draft (frontend half)'
type: 'feature'
created: '2026-11-20'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-1-start-a-draft.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/registry.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The backend lets an owner start a Draft and serves the Field registry, but there is no "Create listing" page, no Drafts list and no Draft page in the frontend.

**Approach:** In the frontend repository (`/home/riyan/Riyan/projects/estate-ai-frontend`, branch `story/2-1-start-a-draft`; the client is already synced) add, inside the signed-in area, a "Create listing" page that renders its choices from `GET /api/v1/registry` (never hard-coded), a "My listings" page that lists the owner's Drafts, and a minimal Draft page that shows what exists so far. Individuals and Agencies see them; administrators do not. Functionality first, simple token-based Atlas styling, no business rules in the frontend (it renders the registry and shows server answers). Only the manual path exists (the AI-assisted path arrives in Epic 5).

## Boundaries & Constraints

**Always:**
- Same-origin typed client calls through `getApi` and the proxy; reuse `withTimeout`, `readErrorBody`, `rateLimitMessage`, `withCsrf` for the create call, the shared form pieces (`Field`, `ErrorSummary`, `useFormFeedback`), `useProfileLoad`-style loading with Retry kept mounted, the guard (`requireAccount`) on every page, `Cache-Control: no-store` for `/account/listings` and below via `next.config.ts` (the existing `/account/:path*` rule already covers them: verify), and the accessibility conventions of the profile pages. No listing id, owner data or registry content goes to storage, console or URL beyond the Draft id in its own path.
- **Registry hook** (`src/lib/use-registry.ts(x)`): fetches `GET /api/v1/registry` once per page load (the browser honours the server's ETag and cache header), exposes `{status: loading|ready|error, registry, retry}` with a typed accessor (`categories`, `subtypesOf(category)`, `purposes`), validates the shape defensively (a malformed registry is an error with Retry, never a crash), and derives human labels by one function `label(id)` (snake_case to "Sentence case": `plot_file` becomes "Plot file") because the registry carries ids only. Choices come only from the registry; nothing about purposes, Categories or Subtypes is hard-coded in the frontend (a test proves a changed registry in the stub changes the choices).
- **Routes** (all in `src/app/(app)/account/listings/`): `/account/listings` ("My listings"), `/account/listings/new` ("Create listing"), `/account/listings/[id]` (Draft page). Header and dashboard get a "My listings" link for individual and agency accounts only (hidden for administrators; an administrator who opens a route directly sees the polite "Listings are available for individual and agency accounts." message with no form, driven by the server's `403`). Sign-in redirects keep `next` (`/account/listings`...); an administrator whose session is gone goes to the admin sign-in as on the other shared pages.
- **Create listing** (`/account/listings/new`): a form with Purpose (radio group: sale, rent), Category (select) and Subtype (select, enabled only after a Category is chosen and listing only that Category's Subtypes; changing Category clears an incompatible Subtype); all three required with labelled fields, error summary with focus, one announcement mechanism, busy state, no double submit, `method="post"`, client required checks that send nothing; submit `POST /api/v1/listings` through `withCsrf` with exactly `{purpose, category, subtype}`; `201` navigates to `/account/listings/<id>` (router push, a created Draft is shown there); `400` field errors mapped from the server envelope (`required`, `invalid`, `invalid_choice`, the distinct subtype-mismatch message) with the summary focused and the values kept; `403 forbidden` (a Suspended account, or an administrator) shows "Your account cannot create listings right now." with no retry loop; `429` (the 20-open-Draft cap, no `Retry-After`) shows "You have reached the limit of 20 open drafts. Finish or remove one before starting another." with a link to My listings; `401` flips the provider to signed out and goes to sign-in with `next`; 5xx, timeout and network failures show their own generic messages and keep the form; a second 403 after the one CSRF retry is the generic permission message. A short note on the page says the AI-assisted path will be added later (no control for it).
- **My listings** (`/account/listings`): loads `GET /api/v1/me/listings` (default page) with loading, error with Retry (kept mounted) and a `401` redirect; items newest first showing the Draft's summary built from its fields via the registry labels ("Sale · House · House", status "Draft", created time as a `<time>`), each linking to its Draft page; "Load more" follows the server `next` cursor and appends without duplicates (dedupe by id), hidden when `next` is null; an empty state ("You have no listings yet." with a "Create listing" link); a "Create listing" button at the top; a failed load-more keeps the list with a retry message; an unknown or missing field renders as "Not set" and never crashes; the list must not flash content for a signed-out person (guarded). 403 shows the administrator message.
- **Draft page** (`/account/listings/[id]`): loads `GET /api/v1/listings/{id}`; shows the status, revision, registry version and the three fields with labels, a note that the remaining details can be added in later steps (nothing editable yet), and a link back to My listings; `404` (unknown, someone else's, malformed id, administrator) shows "We could not find that listing." with a link back, never revealing which; failures use the shared error state with Retry.
- **Shared code**: reuse and, where the notification list or profile pages would otherwise be copied verbatim, extend the existing hooks rather than add a third loader; do not add a data library.
- Tests (Playwright against the stub backend, which gains `GET /api/v1/registry` (serving the real taxonomy of Category and Subtype ids, with ETag), `POST /api/v1/listings`, `GET /api/v1/listings/{id}`, `GET /api/v1/me/listings` (cursor `next` links, 20 per page) with modes for the matrix rows, stateful so created Drafts appear in the list and in the detail, reusing the session and CSRF stub) cover every matrix row; axe at desktop and mobile on the main states; existing suites keep passing (header tests change for the new link); lint with `--max-warnings 0`, typecheck, build and `api:check` pass; README documents the pages, the registry hook, the stub modes and the hand-off to Story 2.2. IMPORTANT: gitleaks flags password-looking high-entropy literals: use only low-entropy fake-word values and repetitive digit patterns for ids; assert on the element that owns a text (never page-wide `getByText`; the retro found duplicate sr-only live regions make those flaky); keep the full-run time in mind and do not add serial mode.

**Never:** hard-code purposes, Categories or Subtypes; show another account's listing or hint that an id exists (404 copy is identical for every cause); send any field the form does not own; offer the create control to administrators; put registry data, ids or counts in storage, console, title or URL beyond the Draft id path; poll on a timer; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Registry loads | server taxonomy | purposes, Categories and the chosen Category's Subtypes render from it | N/A |
| Registry changes | stub serves another taxonomy | the choices change with it (no hard-coded values) | test |
| Registry fails or malformed | 5xx, timeout, bad shape | message with Retry, no crash, no form | N/A |
| Choose Category | Category picked | Subtype enabled with only that Category's Subtypes; changing Category clears an incompatible Subtype | N/A |
| Plot File | plot then plot_file | allowed, label "Plot file" | N/A |
| Create ok | valid trio | exact body `{purpose, category, subtype}`; navigates to the Draft page | N/A |
| Required checks | any value missing | client errors, no request | N/A |
| Field errors | 400 codes incl. subtype mismatch | mapped text, summary focus, values kept | N/A |
| Suspended | 403 | "Your account cannot create listings right now.", no retry loop | N/A |
| Cap reached | 429 | the 20-drafts message with a link to My listings | N/A |
| Session gone | 401 | signed out, redirect to sign-in with `next`; administrators to the admin sign-in | N/A |
| Failures | 5xx, timeout, network, second 403 | separate generic messages, form kept | N/A |
| CSRF self-heal | first POST 403 | token refetched, retried once | second 403 generic |
| Double submit | click twice | exactly one request | N/A |
| My listings | owner with drafts | newest first, summary from labels, links to Draft pages | N/A |
| Load more | `next` present | appends without duplicates; hidden at the end; failure keeps the list with a retry message | N/A |
| Empty | no drafts | empty state with a Create listing link | N/A |
| Unknown field value | `null` or unknown id | "Not set"/humanised id, no crash | N/A |
| Draft page | own Draft | status, revision, registry version, three fields, back link | N/A |
| Draft 404 | unknown, other's, malformed id | identical "could not find" copy and link back | N/A |
| Administrator | admin session | no My listings link; direct visit shows the polite message; no form | N/A |
| Guard | Visitor opens any route | redirect to sign-in with `next`; no content | N/A |
| Links | individual, agency, admin | My listings link in the header and dashboard per type | N/A |
| Privacy | after any state | no listing or registry data in storage, console, title or URL beyond the id path | test |
| Accessibility | list, create form with errors, Draft, empty and error states | axe clean at desktop and mobile; one live region announces once | test |

</frozen-after-approval>

## Code Map

- `src/lib/use-registry.tsx`, `src/lib/listing-labels.ts` (label and summary helpers)
- `src/app/(app)/account/listings/{page,listings-client}.tsx`, `new/{page,create-listing-form}.tsx`, `[id]/{page,draft-client}.tsx`
- `src/components/site-header.tsx`, `src/app/(app)/dashboard/page.tsx` (links), `src/lib/password-messages.ts` or a listing messages module (codes to text), `next.config.ts` only if the existing rule does not cover the new routes
- `tests/e2e/stub-backend.mjs`, `tests/e2e/listings.spec.ts`, README

## Tasks & Acceptance

**Execution:**
- [ ] registry hook and label helpers
- [ ] create listing form, My listings and Draft pages, header and dashboard links
- [ ] stub modes, Playwright tests, README

**Acceptance Criteria:**
- Given the frontend, when I open "Create listing", then I see the manual path and choose sale or rent, a Category and a Subtype rendered from the registry, and the Draft appears in my Drafts.
- Given an invalid pairing or a missing choice, when I submit, then I see field-level errors.
- Given I already have 20 open Drafts, when I start another, then I am told the limit is reached.
- Given a Suspended account or an administrator, when it opens the page, then it cannot create a Draft.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Design Notes

Decisions made without asking: the choices come from the registry at runtime so the frontend never owns the taxonomy; human labels are derived from ids because the registry carries ids only (a later story may add labels to the registry); the Draft page is deliberately minimal and read-only until Story 2.2 adds editing; the own-listings routes live under `/account/listings` so the public discovery routes can own `/listings` later; the not-found copy is identical for every cause so a foreign Draft id is never confirmed.

## Verification

**Commands (frontend repository):**
- `npm run api:sync && npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` -- expected: all pass (the full Playwright run takes about 16 to 20 minutes: run it in the background with output to a file; never run two Playwright runs at once)
