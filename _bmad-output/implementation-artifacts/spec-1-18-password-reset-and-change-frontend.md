---
title: 'Story 1.18: Password reset and change (frontend half)'
type: 'feature'
created: '2026-11-19'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-18-password-reset-and-change.md'
  - '{project-root}/docs/password-reset.md'
  - '{project-root}/docs/sessions.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-17-sign-in-and-sign-out-frontend.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-16-email-verification-frontend.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The backend can reset and change passwords, but the frontend has no forgot-password form, no page behind the emailed link, and no way for a signed-in person to change their password.

**Approach:** In the frontend repository (`/home/riyan/Riyan/projects/estate-ai-frontend`, branch `story/1-18-password-reset-and-change`; the client is already synced) add `/forgot-password`, `/reset-password` (the emailed link target) and a signed-in `/account/password` page, plus the links between them. Functionality first, simple token-based Atlas styling, no business rules in the frontend (it shows server answers and codes).

## Boundaries & Constraints

**Always:**
- Same-origin typed client calls through `getApi` and the proxy; reuse `withTimeout`, `readErrorBody`, the shared messages and `rateLimitMessage` from `src/lib/forms.ts`, the CSRF helper (`withCsrf`) for the signed-in change, and the accessibility pattern of the register and sign-in forms (labelled fields, error summary with focus, one announcement mechanism, busy state, no double submit, `noValidate` with client required checks that send nothing). No token, password or email goes to storage, console, URL (except the emailed token on arrival) or logs.
- **`/forgot-password`** (public): email field; submit `POST /api/v1/password-reset-requests`; `202` shows ONE neutral confirmation ("If that address has an account, we have sent a link. It works once and expires in 1 hour.") whatever the address, with the typed address trimmed; `429` shows the rate-limit message from `Retry-After`; `400` field errors against the email field; 5xx, timeout, network and rejected requests show their own generic messages. Never any wording that implies whether the address exists. Link back to sign in.
- **`/reset-password`** (public, the emailed link): reads `token` from the query string, removes it from the address bar immediately (`history.replaceState`, keeping other parameters), keeps it only in memory, sends no confirm until the person submits a new password (the link is not consumed on load). `Referrer-Policy: no-referrer` and `Cache-Control: no-store` for the route, and the page stays statically prerendered (do not use `force-dynamic`: it would put the token in the served HTML; this was verified in Story 1.16). A missing or blank token shows the "link has no verification code"-style missing state with a link to request a new one. Form: New password with show/hide control (`autocomplete="new-password"`), submit `POST /api/v1/password-resets/confirm` `{token, password}`. `200` shows success ("Your password has been changed. Sign in with your new password.") with a link to `/sign-in`, and clears the token from memory; the header state is refreshed (all sessions ended server-side). Errors: `link_expired`, `link_used`, `link_invalid` each show their own state with a link to `/forgot-password` ("Request a new link"), the token dropped; `400 validation_error` with `fields.password` shows the mapped policy message under the field (map `required`, `password_too_short`, `password_too_long`, `password_matches_email`, `password_too_common`, `invalid` to human text, shared with the register form if it already has the map) and the token and the form stay usable; `429` shows the rate-limit message; 5xx, timeout, network show retry messages and keep the form. Never show the server's raw messages for unknown codes.
- **`/account/password`** (signed-in area, guarded like the other `(app)` pages via `requireAccount`): fields Current password (`autocomplete="current-password"`) and New password (`new-password`), both with show/hide; submit `POST /api/v1/me/password` through `withCsrf`. `200` shows success ("Your password was changed. Your other devices were signed out."), clears both fields, and refreshes the session provider. `400` `fields.current_password = incorrect` shows "The current password is not correct." against that field; `fields.new_password` codes (policy codes and `password_same_as_current` → "Choose a password different from your current one.") against the new field; `401 unauthenticated` (session gone) flips to signed out and sends the person to `/sign-in?next=/account/password`; second `403`, 5xx, timeout and network show generic messages. Empty fields show required errors and send nothing. The header gains a "Change password" link for signed-in accounts and the dashboard links to it.
- Sign-in page gains a "Forgot your password?" link to `/forgot-password`; the success notice on `/reset-password` links to `/sign-in`.
- The shared field-message map for policy codes lives in one module and the register form uses it too if it duplicates it (refactor only if the duplication exists).
- Tests (Playwright against the stub backend, which gains `/api/v1/password-reset-requests`, `/api/v1/password-resets/confirm` and `/api/v1/me/password` with modes for the matrix rows below, reusing the stateful session/CSRF stub from Story 1.17) cover every matrix row; axe at desktop and mobile on the main states of the three pages; existing suites keep passing; lint, typecheck, build and `api:check` pass; the production headers and static prerender of `/reset-password` are checked with `next build && next start`.

**Never:** put the token, a password or an email in storage, console or logs; keep the token in the URL after arrival; fire the confirm on load; show different confirmations for registered vs unregistered addresses; follow a `next` that is not validated by `safeNext`; read the session cookie in script; decide permissions beyond showing server answers; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Forgot ok | any email, 202 | one neutral confirmation, address trimmed in the request, no existence hint | N/A |
| Forgot throttled | 429 + Retry-After | rate-limit message (minutes), form stays usable | N/A |
| Forgot failures | 5xx, timeout, network, 400 field | separate generic messages; field error on the email | N/A |
| Forgot double submit | click twice | exactly one request | N/A |
| Reset link arrives | `/reset-password?token=T&utm=a` | token removed from the URL, `utm` kept, no request sent yet | N/A |
| Reset missing token | no or blank token | missing state with link to `/forgot-password`, no confirm possible | N/A |
| Reset ok | valid link + valid password | success with sign-in link; request body is exactly `{token, password}`; token cleared; header refreshed | N/A |
| Reset expired / used / invalid | 410 / 409 / 400 codes | own state each, link to request a new one, form gone | N/A |
| Reset weak password | 400 `fields.password` code | mapped message under the field; form usable; same token resubmits successfully | N/A |
| Reset failures | 429, 5xx, timeout, network | rate-limit or generic retry message; form and token kept | N/A |
| Reset privacy | after any state | no token or password in storage, console, URL, rendered HTML outside the field; headers `no-referrer`, `no-store`; page prerendered | test |
| Change ok | right current, valid new | success text, fields cleared, provider refreshed | N/A |
| Change wrong current | 400 `incorrect` | message on the current field, focus summary | N/A |
| Change policy errors | 400 new-password codes incl. same-as-current | mapped messages | N/A |
| Change CSRF self-heal | first 403 | token refetched, retried once, success | second 403 generic |
| Change session gone | 401 | signed out, redirect to `/sign-in?next=%2Faccount%2Fpassword` | N/A |
| Change guard | Visitor opens `/account/password` | redirect to sign-in with `next`; no content | N/A |
| Change failures | 5xx, timeout, network, empty fields | generic messages; empty sends nothing | N/A |
| Links | sign-in, header, dashboard | "Forgot your password?", "Change password" present as specified | N/A |
| Accessibility | main states | axe clean at desktop and mobile | test |

</frozen-after-approval>

## Code Map

- `src/app/(public)/forgot-password/page.tsx`, `forgot-password-form.tsx`
- `src/app/(public)/reset-password/page.tsx`, `reset-password-client.tsx` (token handling like verify-email)
- `src/app/(app)/account/password/page.tsx`, `change-password-form.tsx`
- `src/lib/password-messages.ts` (shared policy-code map), `next.config.ts` (headers for `/reset-password`), `src/components/site-header.tsx`, sign-in page, dashboard links
- `tests/e2e/stub-backend.mjs`, `tests/e2e/password.spec.ts`, README

## Tasks & Acceptance

**Execution:**
- [ ] forgot-password page and form
- [ ] reset-password page with in-memory token, states and headers
- [ ] change-password page, links, shared message map
- [ ] stub modes, Playwright tests, README

**Acceptance Criteria:**
- Given the reset form, when I submit any email, then I see the same confirmation whether or not it is registered.
- Given a valid reset link, when I set a new password meeting the policy, then it changes, the link cannot be reused, and I am told to sign in.
- Given I am signed in, when I change my password with my current password, then I am told it worked and that my other devices were signed out.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, frontend half (blind, edge-case, verification-gap).

**Patched:**
- Change form: 429 on the POST shows the wait; timeout and network add a hint that the change may already have happened; success message clears on edit.
- Forgot form: email field codes mapped, raw server message never shown.
- Reset page: a 400 flagging the token (or lacking a password field) ends as an invalid link; CSRF cache cleared on success; one announcement mechanism for failures.
- Tests (12 new): `next.config.ts` header rules asserted directly (dev responses cannot fail on no-store), provider refresh after change, distinct-sentence and unknown-code fallbacks, all six policy codes for each map, CSRF fetch failures on the change page, forgot privacy test, unknown-code 410 shows no raw text, edit clears confirmations.

**Rejected:** CSRF header on the anonymous forgot and reset POSTs. The backend public views ignore the session cookie (`authentication_classes = []`) and need none; backend tests prove a 400 or 202 with a session cookie and no token. Documented in the README.

**Deferred:** token in a URL fragment (documented trade-off, same as Story 1.16), shared form scaffolding hook (five forms repeat it, extract with the next form), hidden username field for password managers, lockout hint on the incorrect-password message, trailing-slash header variants, automated `next build && next start` header check (manual check documented), reset-ok header flip when previously signed in.

Deviation recorded: the backend sends static field sentences, not bare codes; the maps accept both and show an unknown sentence as sent only when it is a known static server sentence, otherwise the generic text.

## Design Notes

Decisions made without asking: the reset page never fires the confirm on load because the person must type a password, so no prefetch concern arises, but the token is still scrubbed from the URL at once; the page stays static so the token never reaches the served HTML; policy codes map to human text in one shared module; the change page flips to signed out on a 401 and returns with `next` so a stale session recovers cleanly.

## Verification

**Commands (frontend repository):**
- `npm run api:sync && npm run lint && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` -- expected: all pass
