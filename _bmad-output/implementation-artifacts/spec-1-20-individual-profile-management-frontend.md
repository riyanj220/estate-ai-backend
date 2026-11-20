---
title: 'Story 1.20: Individual profile management (frontend half)'
type: 'feature'
created: '2026-11-20'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-20-individual-profile-management.md'
  - '{project-root}/docs/profile.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-18-password-reset-and-change-frontend.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-16-email-verification-frontend.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The backend can read and update an Individual's profile and run a verified email change, but there is no profile page, no way to request an email change, and no page behind the emailed confirmation link.

**Approach:** In the frontend repository (`/home/riyan/Riyan/projects/estate-ai-frontend`, branch `story/1-20-individual-profile-management`; the client is already synced) add a signed-in `/account/profile` page (view and edit name and phone, request an email change) and a public `/confirm-email-change` page that confirms the emailed token. Functionality first, simple token-based Atlas styling, no business rules in the frontend (server-driven validation, the frontend only shows server answers and codes). Picture upload is out of scope (Epic 2).

## Boundaries & Constraints

**Always:**
- Same-origin typed client calls through `getApi` and the proxy; reuse `withTimeout`, `readErrorBody`, shared messages, `rateLimitMessage`, `withCsrf` for every signed-in unsafe call, the shared form pieces (`Field`, `PasswordField`, `ErrorSummary`, `useFormFeedback`, `registration-messages`, `password-messages`), and the patterns of the register, change-password and verify-email pages. Every form uses `method="post"` and the established accessibility pattern (labelled fields, error summary with focus, single announcement mechanism, busy state, no double submit, `noValidate` with client required checks that send nothing). No phone, email, password or token goes to storage, console or URL (except the emailed token on arrival at `/confirm-email-change`).
- **`/account/profile`** (signed-in area, guarded with `requireAccount`, `Cache-Control: no-store` via `next.config.ts`, linked from the header and the dashboard as "Profile"): loads `GET /api/v1/me/profile` on the client (`{full_name, phone, email, pending_email}`) with a loading state and a retry on failure; a non-Individual account (403 `forbidden`) sees a polite "Profile editing is available for individual accounts." message with no form; a `401` flips the session provider to signed out and goes to `/sign-in?next=%2Faccount%2Fprofile`.
  - **Details form** (full name, phone): both required, prefilled from the server; submit `PATCH /api/v1/me/profile` through `withCsrf` sending only the fields that changed (nothing changed: no request, a polite "No changes to save." note); `200` shows "Your profile was saved." and refreshes the form from the response and the session provider (so the header name updates); `400` field errors (`required`, `invalid`, `invalid_name`, `invalid_phone`, `unknown_field`/non-field) are mapped to human text against the fields with the error summary focused; 403 after the one CSRF retry, 429, 5xx, timeout and network show their own generic messages; the saved success message clears when the user edits a field. The phone is shown only on this page, in an input, never in the header or elsewhere.
  - **Email section**: shows the current address (read-only) and, when `pending_email` is set, a plain note "We sent a confirmation link to <pending_email>. Your sign-in address stays <email> until you open it." (the address is the owner's own, shown in text only); form fields New email address (`autocomplete="email"`) and Current password (`current-password`, show/hide); submit `POST /api/v1/me/email-change` through `withCsrf` with exactly `{new_email, current_password}`; `202` shows ONE neutral confirmation ("If that address can be used, we have sent it a confirmation link. Your sign-in address does not change until you open it.") whatever the address, clears the password, and re-reads the profile (so `pending_email` appears); never any hint about whether the address is registered. `400` maps `new_email` codes (`required`, `invalid`, `invalid_email`) and `current_password: incorrect` ("The current password is not correct.") against the fields; `429` shows the rate-limit wait; 403, 5xx, timeout, network show generic messages. The two forms are independent (one error summary and one live-region mechanism per form, unique ids).
- **`/confirm-email-change`** (public, the emailed link): same token handling as `/reset-password` and `/verify-email`: read `token` from the query, remove it from the address bar immediately (`history.replaceState`, other params kept), keep it only in memory, `Referrer-Policy: no-referrer` and `Cache-Control: no-store` for the route, page statically prerendered (never `force-dynamic`: it would put the token in the served HTML). A missing or blank token shows a "link is not valid" state with a link to `/account/profile` ("Request the change again"). Confirmation is a button the person presses ("Confirm the change"), not automatic on load, so a link prefetch cannot consume it; submit `POST /api/v1/email-changes/confirm` with `{token}`; `200` shows "Your sign-in email address was changed. Use the new address to sign in next time." with links to `/sign-in` and `/account/profile`, clears the token, and refreshes the session provider; `link_expired`, `link_used`, `link_invalid` each show their own state with a link to `/account/profile` and drop the token; `429` shows the rate-limit message; 5xx, timeout and network keep the button and token with a retry message; unknown codes show a generic message (never raw server text); a 400 whose code is not a link code is treated as `link_invalid`.
- Shared code: add `PROFILE_MESSAGES` (field codes to human text) to the existing message modules rather than a new copy; reuse the check-your-email style confirmation component where it fits.
- Tests (Playwright against the stub backend, which gains `/api/v1/me/profile` GET and PATCH, `/api/v1/me/email-change` and `/api/v1/email-changes/confirm` with modes for the matrix rows, reusing the stateful session and CSRF stub) cover every matrix row; axe at desktop and mobile on the main states of both pages; existing suites keep passing; lint, typecheck, build and `api:check` pass; the production headers and static prerender of `/confirm-email-change` are verified with `next build && next start` (and the served HTML must not contain the token); README documents pages, stub modes and the hand-off to Story 1.21. IMPORTANT: gitleaks flags password-looking high-entropy literals: use only low-entropy fake-word passphrases such as "correct horse battery staple" in tests and the stub.

**Never:** put the phone, an email address, a password or a token in storage, console, logs or the URL (beyond the arrival token); show the phone outside the profile form; send an unchanged profile; fire the confirm on load; reveal whether a new address is registered; follow a `next` not validated by `safeNext`; show raw server text for unknown codes; keep the token after a terminal state; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Load | signed-in Individual | form prefilled with name and phone; current email shown; pending note when `pending_email` | N/A |
| Load fails | 5xx, timeout, network | message with Retry; nothing editable | N/A |
| Wrong account type | 403 | polite "individual accounts only" message, no form | N/A |
| Session gone | 401 | signed out, redirect to `/sign-in?next=%2Faccount%2Fprofile` | N/A |
| Guard | Visitor opens the page | redirect to sign-in with `next`; no content | N/A |
| Save name or phone | changed values | PATCH body has only the changed fields; success note; header name refreshed | N/A |
| Nothing changed | no edits | no request; "No changes to save." | N/A |
| Field errors | 400 codes | mapped text under fields, summary focus, values kept | N/A |
| Phone required | blank phone | client required error, no request; server `required` mapped too | N/A |
| Edit after save | typing | success note clears | N/A |
| Save failures | 429, 5xx, timeout, network, second 403 | separate generic messages | N/A |
| CSRF self-heal | first 403 | token refetched, retried once | second 403 generic |
| Email request ok | valid address and password | exact body `{new_email, current_password}`; neutral confirmation; password cleared; pending note appears after re-read | N/A |
| Email request, taken or same address | server answers the same 202 | identical neutral confirmation | N/A |
| Email request errors | 400 `incorrect`, `invalid_email`, 429, 5xx, timeout, network | mapped messages; wait time on 429; password cleared on 400 | N/A |
| Double submit | click twice | exactly one request per form | N/A |
| Confirm arrives | `/confirm-email-change?token=T&utm=a` | token removed from the URL, `utm` kept, no request until the button is pressed | N/A |
| Confirm missing token | no or blank | invalid-link state with link to the profile page | N/A |
| Confirm ok | valid link | body exactly `{token}`; success with sign-in and profile links; token cleared; session refreshed | N/A |
| Confirm expired / used / invalid | 410 / 409 / 400 | own state each, link to the profile page, button gone | N/A |
| Confirm failures | 429, 5xx, timeout, network | message; button and token kept | N/A |
| Confirm unknown code | unknown error code | generic message, never raw server text | N/A |
| Privacy | after any state | no phone, email, password or token in storage, console, URL or rendered HTML outside the intended fields; confirm headers `no-referrer`, `no-store`; page prerendered | test |
| Accessibility | main states | axe clean at desktop and mobile | test |

</frozen-after-approval>

## Code Map

- `src/app/(app)/account/profile/page.tsx`, `profile-client.tsx` (loader), `details-form.tsx`, `email-change-form.tsx`
- `src/app/(public)/confirm-email-change/page.tsx`, `confirm-email-change-client.tsx`
- `src/lib/*messages*.ts` (profile codes), `next.config.ts` (headers for `/confirm-email-change` and `/account/:path*` already covers the profile), `src/components/site-header.tsx`, dashboard link
- `tests/e2e/stub-backend.mjs`, `tests/e2e/profile.spec.ts`, README

## Tasks & Acceptance

**Execution:**
- [ ] profile page: loader, details form, email change form
- [ ] confirm-email-change page with in-memory token and private headers
- [ ] links, message maps
- [ ] stub modes, Playwright tests, README

**Acceptance Criteria:**
- Given I am signed in, when I open my profile page, then I can view and edit my name and phone with validation messages.
- Given I request an email change, when I submit a new address, then I am told a confirmation link was sent without learning whether the address is registered, and my sign-in address stays until I confirm.
- Given the emailed link, when I confirm it, then I am told my sign-in address changed.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, frontend half (blind, edge-case, verification-gap).

**Patched:**
- Existence oracle: the pending note appeared only for free addresses because the backend showed `pending_email` only for issued links. Fixed in the backend (`pending_email` now identical across free, taken, same and capped requests, merged as a separate fix) and the note wording is neutral ("If that address can be used...").
- Emailed token restored in `history.state` by Next after Back or Forward (the address bar was clean): new `scrub-token` helper rewrites the router state; applied to the confirm page and, as the same latent leak, to the reset and verify pages with Back/Forward tests on all three.
- Server text: only known codes are shown, unknown sentences, unknown keys and prototype keys (`constructor`, `__proto__`) become generic text; `mapFieldMessages` uses `Object.hasOwn`.
- Details form: typed edits survive an in-flight save, trim-only changes, malformed 2xx bodies, unhandled `refresh()` rejections; email form: sent text clears on edit, new email `autocomplete=off`; profile re-reads silently on focus and visibility; Retry keeps focus; Profile links only for individual accounts; confirm sends a locally captured token.
- Tests (17 new): session-refresh assertions that fail without the refresh call (also fixed in the reset and change tests), served markup has no token, unsaved edits survive an email request, privacy scan after 400 and 5xx.

**Rejected:** retry on any 403 double-counting the password throttle (the CSRF check rejects before the backend service; comment added).

**Deferred:** validating required fields only when changed, one shared 400-handling helper across forms, a `useProfile` hook, double announcement through `showNotice` (existing shared pattern), double-click guard test on the confirm button, automated production prerender check (manual `next build && next start` check documented).

Deviation: an unknown-code 409 or 410 on the confirm page keeps the button with a generic message (documented in the README).

## Design Notes

Decisions made without asking: the profile loads on the client so the page stays behind the existing guard and reuses the session provider; only changed fields are sent so the audit trail stays meaningful; the confirm page needs a button press so link scanners and prefetchers cannot consume the single-use token (the backend also requires POST); the pending-address note is shown in text only and the phone never leaves the profile form; the confirm page stays static with private headers exactly like the reset page.

## Verification

**Commands (frontend repository):**
- `npm run api:sync && npm run lint && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` -- expected: all pass
