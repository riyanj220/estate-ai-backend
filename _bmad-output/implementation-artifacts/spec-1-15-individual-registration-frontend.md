---
title: 'Story 1.15: Individual registration (frontend half)'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'af8440727e722f751b1e2821ad9815f5cd59730e (frontend repository)'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-15-individual-registration.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-5-frontend-scaffold-and-generated-api-client.md'
  - '{project-root}/docs/registration.md'
  - '{project-root}/_bmad-output/planning-artifacts/ux-designs/ux-estate-ai-backend-2026-11-19/.memlog.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The backend registration endpoint exists but a Visitor has no page to use it.

**Approach:** In the frontend repository (`/home/riyan/Riyan/projects/estate-ai-frontend`, branch `story/1-15-individual-registration-page`), regenerate the typed client from the merged backend `openapi.yaml` and build a `/register` page with an accessible form that shows field-level errors from the standard error envelope, the rate-limit message and the success state. Functionality first: simple, token-based styling in the provisional Atlas direction, no visual polish.

## Boundaries & Constraints

**Always:** work only in the frontend repository on the named branch (planning and spec files stay in the backend repository); run `npm run api:sync` so `src/api/openapi.yaml` and `src/api/schema.ts` match the backend `main` (the sync script already exists; the generated directory is never hand-edited); the page is `src/app/(public)/register/page.tsx` with the interactive form as a client component calling `POST /api/v1/registrations/individuals` through the typed client on the same origin (`/api` proxy); the form has four fields in this order: full name, email address, password, phone number, each with a visible label, correct `type`, `name`, `autocomplete` (`name`, `email`, `new-password`, `tel`), `inputmode` where useful and a short hint for the phone (for example "Pakistani mobile like 0300 1234567, or an international number starting with +"); no role choice is shown or sent, and the request body contains exactly the four fields; the frontend contains no business rules: it does not re-implement password, name, email or phone validation (only the HTML `required` attribute and what the server returns), it trims nothing silently except the leading and trailing spaces of the email field, and it never stores or logs the password or phone anywhere (component state only, cleared after success); on `400` it maps `error.fields` (keys `full_name`, `email`, `password`, `phone`, `non_field_errors`) to messages shown under the matching input and in an error summary at the top that links to each invalid field, moves focus to the summary, sets `aria-invalid` and `aria-describedby` on invalid inputs, and shows the server's static messages verbatim; on `429` it shows the limit message including the wait time derived from the `Retry-After` header (in minutes, rounded up) and keeps the entered values; on `202` it shows the success state "Check your email" (generic wording, shows the typed email address from local state, says to look in spam, never says whether the address already existed) and clears the password and phone from state; on a network or `5xx` error it shows a generic retry message and keeps the entered values; the submit button is disabled while the request is pending and announces the pending state; the password input has a show and hide control (a real button with `aria-pressed`); every state change that matters is announced through a polite live region; the form is fully keyboard operable with visible focus; the page and its states pass axe checks (no violations) on desktop and mobile widths; styles use CSS variables for the Atlas tokens recorded in the UX memlog (background #EEF0F3, surface #FFFFFF, text #14181F, accent #0A6C92, system sans, radius at most 6px) defined once in the global stylesheet so a later design pass can restyle cheaply; a "Create an account" link on the public home page points to `/register`; lint, type check, build, `api:check` and the Playwright suite pass; the frontend CI workflow keeps passing; code comments only where intent is not obvious.

**Never:** add sign-in, verification, profile or any other page, add client-side validation rules beyond `required`, send or show a role field, log or persist entered values, call the backend directly from the browser (always the same-origin proxy), hand-edit `src/api/`, change the backend repository's code, or add a UI component library or form library.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Page | visit `/register` | heading, four labelled fields, submit button, privacy note that the phone stays private | N/A |
| Success | stub returns `202 {"status":"check_your_email"}` | success state with the typed email, password and phone cleared, no form errors | N/A |
| Field errors | stub returns `400` with `fields` for several inputs | summary lists each invalid field with a link, focus moves to the summary, messages under inputs, `aria-invalid` set | static server messages shown verbatim |
| Non-field error | `non_field_errors` present | shown in the summary | N/A |
| Rate limited | stub returns `429` with `Retry-After: 1800` | message "Too many attempts. Try again in 30 minutes."; values kept | N/A |
| Server or network error | stub returns `500` or the connection fails | generic retry message; values kept; button re-enabled | no technical detail shown |
| Pending | stub delays the response | button disabled, pending state announced, double submit prevented | N/A |
| Request body | any submission | exactly `full_name`, `email`, `password`, `phone`; no role field | asserted in a test |
| Password control | press the show button | password revealed, `aria-pressed` true; press again hides | N/A |
| Keyboard | tab through the form and submit with Enter | works end to end with visible focus | N/A |
| Accessibility | axe on initial, error, rate-limited and success states at desktop and mobile widths | no violations | N/A |
| Contract | `npm run api:check` | client is up to date with the backend `main` contract | N/A |
| Home link | public home page | contains a link to `/register` | N/A |
| Privacy | browser storage and console after a submission | no entered values stored or logged | asserted in a test |

</frozen-after-approval>

## Code Map

- `src/api/` -- regenerated by `npm run api:sync` (do not hand-edit)
- `src/app/(public)/register/page.tsx` and `register-form.tsx` -- page and client form component; `src/app/(public)/page.tsx` -- add the link
- `src/app/globals.css` -- Atlas tokens as CSS variables and base form styles
- `src/lib/api.ts` -- reuse the existing typed client builder; add a small helper to read the standard error envelope from a failed response (typed by the generated schema where possible)
- `tests/e2e/stub-backend.mjs`, `tests/e2e/register.spec.ts` -- extend the stub with the registration route and its modes (accepted, field errors, rate limited, server error, hang, echo of the received body); tests for every matrix row, including axe states and a mobile viewport
- `README.md` -- document the page and how to run it against the real backend

## Tasks & Acceptance

**Execution:**
- [x] run `npm run api:sync` and commit the regenerated client
- [x] form component, page, home link, CSS tokens
- [x] stub backend routes and Playwright tests (including axe and mobile) for every matrix row
- [x] lint, type check, build, `api:check`, CI; README

**Acceptance Criteria:**
- Given the registration form, when I submit it with a missing or invalid field, then the page lists field-level errors from the backend, moves focus to an error summary and never re-validates rules itself.
- Given valid details, when I submit, then I see the success state and no role was asked or sent.
- Given repeated submissions beyond the limit, when the backend answers 429, then the page shows the limit message with the wait time and keeps what I typed.
- Given the generated client, when `npm run api:check` runs, then it is current with the backend contract.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, frontend half (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | A hung request never recovers (BH) | high | patch | No client timeout; 20 s abort added with a clock-controlled test. |
| 2 | Native browser validation bypasses the server error UX (BH, EC, VG) | medium | patch | `noValidate` added; empty submit tested. |
| 3 | Any other 2xx, 4xx and proxy errors mislabelled; network failure blamed on the server (BH, EC) | medium | patch | Status-class messages and tests. |
| 4 | Focus lost after 429 or 5xx; live region does not re-announce; summary role; toggle label (BH, EC) | medium | patch | Fixed with tests. |
| 5 | The double-submit test never exercised the guard (BH, EC, VG) | medium | patch | `requestSubmit()` twice with polling. |
| 6 | Wait-time wording and 400 fallbacks tested only for one case (VG) | medium | patch | Stub modes added. |
| 7 | Success state is a dead end (BH) | low | patch | "Use a different email address" button (clears the phone too, per the frozen spec). |
| 8 | Stub leaks hung sockets; shared state across tests (EC, VG, BH) | low | patch | Cleanup and serial mode. |
| 9 | Global background change reaches other pages; Tailwind body classes removed (BH, EC) | low | reject | Intentional token change; existing page tests including axe still pass. |
| 10 | openapi.yaml includes cities; schema.ts absent from the diff (BH, EC) | false | reject | Current backend contract; schema.ts excluded from the review diff only. |
| 11 | Phone hint hard-codes a format; HTTP-date Retry-After; terms and sign-in link; success clearing unobservable (BH, VG) | low | reject | Hint is UX copy; other items belong to later stories. |

## Design Notes

Decisions made without asking: the success state shows the typed email from local state because the backend deliberately says nothing about existence; the home page gets one plain link instead of a navigation redesign; error messages come from the server so wording lives in one place; the Atlas tokens are applied through CSS variables only, as the user chose functionality first and a later restyle; no form or validation library keeps the page small and the rules server-side.

## Verification

**Commands (frontend repository):**
- `npm ci && npm run api:sync && npm run lint && npm run typecheck && npm run build && npm run api:check -- --offline && PW_CHANNEL=chrome npx playwright test` -- expected: all pass
