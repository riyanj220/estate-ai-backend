---
title: 'Story 1.19: Agency registration (frontend half)'
type: 'feature'
created: '2026-11-20'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-19-agency-registration.md'
  - '{project-root}/docs/registration.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-15-individual-registration-frontend.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The backend accepts Agency registrations, but there is no page for an agency to register, and the header cannot name an agency account.

**Approach:** In the frontend repository (`/home/riyan/Riyan/projects/estate-ai-frontend`, branch `story/1-19-agency-registration`; the client is already synced) add a dedicated `/register/agency` page, separate from the Individual form, with a city list loaded from the reference endpoint, the same generic success state and error handling as the Individual form, links between the two registration pages, and agency-aware display names. Functionality first, simple token-based Atlas styling, no business rules in the frontend (server-driven field errors).

## Boundaries & Constraints

**Always:**
- **Page** `/register/agency` (public), with its own heading and a link to the Individual form; the Individual `/register` page gains a link to the Agency form and the home page links to both. Fields, all required, labelled, with `autocomplete` hints: Agency name (`organization`), Business email (`email`), Business phone (`tel`), Office address (`street-address`, a textarea, up to the server limit), City (a `<select>` populated from `GET /api/v1/reference/cities`, first option a disabled placeholder "Select a city"), Password (`new-password`, with the show/hide control used elsewhere). Same accessibility pattern as the Individual form: error summary with focus and links to fields, one announcement mechanism, busy state, no double submit, `noValidate` with client required checks that send nothing. No password, email, phone or address in storage, console or URL.
- **Cities**: loaded client-side on mount through the typed client with `withTimeout`; while loading the select is disabled with a "Loading cities" option; on failure (5xx, timeout, network) show a polite inline message with a Retry button and keep the rest of the form usable but block submit until a city is chosen (the required check handles it); an empty list shows "No cities are available right now." and blocks submit with a notice. The chosen value is the city `id` sent as `city_id`.
- **Submit** `POST /api/v1/registrations/agencies` with exactly `{agency_name, email, phone, office_address, city_id, password}` (email trimmed, other values as typed). `202` shows the same generic "Check your email" success state as the Individual form (same wording, mentions the typed email, no hint about whether the address existed), with links to sign in and to request a new verification link if the Individual success state has them; `400 validation_error` maps `fields` to the form fields (`agency_name`, `email`, `phone`, `office_address`, `city_id`, `password`; `non_field_errors`/unknown keys to the summary) using human text for the known codes (`required`, `invalid`, `invalid_name`, `invalid_email`, `invalid_phone`, `invalid_address`, `invalid_city`, the password policy codes, plus static server sentences shown as sent only when they are known static messages, otherwise a generic "Enter a valid value."); `invalid_city` clears the selection and re-fetches the city list; `429` shows the rate-limit message from `Retry-After`; `403` (a signed-in caller) shows "You are already signed in. Sign out to register another account."; 5xx, timeout, network and rejected requests show their own generic messages. Never any wording that implies whether the address exists.
- **Shared code**: reuse `src/lib/forms.ts`, the password message map and the registration form's patterns; extract shared pieces (field message map for registration codes, show/hide password control, summary and focus handling) into shared modules only where the Individual form would otherwise be copied verbatim, and have the Individual form use them too, keeping its tests green. Do not rewrite the Individual form's behaviour.
- **Display name**: `displayName(account)` returns `agency_name` for agency accounts, else `full_name`, else the email (`/me` now carries `agency_name`, empty for non-agency accounts); header and dashboard use it; the dashboard shows the account type ("Agency" or "Individual") as plain text.
- Tests (Playwright against the stub backend, which gains `/api/v1/registrations/agencies` with modes mirroring the Individual registration stub, and `/api/v1/reference/cities` with modes `ok`, `empty`, `error`, `hang`, and a `/me` mode for an agency account) cover every matrix row below; axe at desktop and mobile on the main states; existing suites keep passing; lint, typecheck, build and `api:check` pass; README documents the page, the stub modes and the hand-off to Story 1.20.

**Never:** put the password, email, phone or address in storage, console, logs or the URL; offer an account-type choice that sends anything but this endpoint; imply whether an email is registered; hard-code a city list; decide verification status or any server-owned field in the client; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Page | open `/register/agency` | Agency heading, six labelled fields, link to the Individual form | N/A |
| Cities ok | reference 200 | select lists cities by name, placeholder first | N/A |
| Cities empty | `items: []` | "No cities are available right now."; submit blocked | N/A |
| Cities fail | 5xx, timeout, network | inline message with Retry; Retry reloads | N/A |
| Required checks | any field empty | field errors in the focused summary, no request sent | N/A |
| Submit ok | valid values | request body exactly the six fields with `city_id`; generic check-your-email state | N/A |
| Existing email | server answers the same 202 | identical success state | N/A |
| Field errors | 400 with fields and codes | mapped text under each field, summary focus, form values kept except password cleared | N/A |
| Invalid city | `fields.city_id = invalid_city` | selection cleared, list re-fetched, error shown | N/A |
| Throttled | 429 + Retry-After | rate-limit message, form usable | N/A |
| Signed in | 403 | "already signed in" message | N/A |
| Failures | 5xx, timeout, network, rejected | separate generic messages, form kept | N/A |
| Double submit | click twice | exactly one request | N/A |
| Privacy | after any state | no password, email, phone or address in storage, console or URL | test |
| Links | register pages and home | Individual and Agency pages link to each other; home links to both | N/A |
| Display name | signed-in agency | header and dashboard show `agency_name`, dashboard shows "Agency" | N/A |
| Individual form | existing tests | unchanged behaviour | existing tests |
| Accessibility | main states | axe clean at desktop and mobile | test |

</frozen-after-approval>

## Code Map

- `src/app/(public)/register/agency/page.tsx`, `agency-register-form.tsx`
- shared extractions under `src/lib/` and `src/components/` as needed; `src/app/(public)/register/register-form.tsx` and `src/app/(public)/page.tsx` links
- `src/lib/account.ts` (`displayName`), `src/components/site-header.tsx`, `src/app/(app)/dashboard/page.tsx`
- `tests/e2e/stub-backend.mjs`, `tests/e2e/agency-register.spec.ts`, README

## Tasks & Acceptance

**Execution:**
- [ ] agency page and form with the city select
- [ ] shared extractions, links, display name
- [ ] stub modes, Playwright tests, README

**Acceptance Criteria:**
- Given the Agency registration page, when I open it, then it is separate from the Individual form and offers agency name, business email, business phone, office address, city (from the reference list) and password.
- Given valid details, when I submit, then I see the same generic check-your-email state as an Individual, whether or not the address was already registered.
- Given invalid input, when I submit, then I see field-level errors from the server.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, frontend half (blind, edge-case, verification-gap).

**Patched:**
- A submit before hydration could put the password, phone and address in a GET query: all seven forms now use `method="post"`.
- City loader: Retry no longer unmounts its own focus target, one live region, focus returns to the select, the select is disabled only on first load, honest required messages for failed, empty and loading states, selection reconciled against every reload, malformed items filtered, stale responses ignored.
- Success only on exactly 202 (both registration forms); email field messages allow-listed by code; address newlines collapse to a space; "Use a different email address" keeps the other fields; announce timer cleared; account type label falls back to the raw type; `ErrorSummary` title id overridable.
- Tests (13 new): selection cleared after reload, double submit settles, privacy scan across success, 400, 429 and 5xx, server-driven option lists, malformed and partial lists, single live region, multi-line paste, non-202 2xx, display-name fallbacks, stale response guard.

**Deferred:** porting the agency form's error handling (timeout wording, 403, code maps, clearing the password after a 400) to the Individual form, which is recorded in the README as a follow-up; distinguishing 401 and 403 on the city fetch; a dedicated reconcile test beyond the invalid-city path.

Note: one health-page test (`backend never answers`) failed once in a full run and passed three times alone; a cold-start timing flake, not a regression.

Deviation: the spec's hand-off to "Story 1.20" is 1.21 for the agency profile (1.20 is Individual profile management); the README names both.

## Design Notes

Decisions made without asking: the city list is fetched in the browser so the page stays static and cacheable; an `invalid_city` answer refetches the list because the likely cause is a city deactivated since the page loaded; display names prefer the agency name so the header is meaningful for agencies; shared registration pieces are extracted only where they would otherwise be copied verbatim.

## Verification

**Commands (frontend repository):**
- `npm run api:sync && npm run lint && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` -- expected: all pass
