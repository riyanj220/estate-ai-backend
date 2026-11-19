---
title: 'Story 1.16: Email verification (frontend half)'
type: 'feature'
created: '2026-11-19'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'aedce1f6373ff47a9c33895ab54a894e3568a432'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-16-email-verification.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-15-individual-registration-frontend.md'
  - '{project-root}/docs/email-verification.md'
  - '{project-root}/_bmad-output/planning-artifacts/ux-designs/ux-estate-ai-backend-2026-11-19/.memlog.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The backend sends verification links and can confirm them, but the link opens nothing, and a user whose link expired has no way to ask for a new one.

**Approach:** In the frontend repository (`/home/riyan/Riyan/projects/estate-ai-backend`'s sibling `/home/riyan/Riyan/projects/estate-ai-frontend`, branch `story/1-16-verification-result-page`), regenerate the typed client and build the `/verify-email` result page that confirms the token from the link and shows success and each failure, with a request-a-new-link form. Functionality first, simple token-based Atlas styling.

## Boundaries & Constraints

**Always:** work only in the frontend repository on the named branch (specs stay in the backend repository); run `npm run api:sync` and commit the regenerated client (never hand-edit `src/api/`); the page is `src/app/(public)/verify-email/page.tsx` with a client component that reads the `token` query parameter, immediately removes it from the address bar with `history.replaceState`, and then sends exactly one `POST /api/v1/email-verifications/confirm` with body `{"token": "<token>"}` through the typed client on the same origin (a ref guard prevents a second request under React strict mode or re-render); the token lives only in a component ref or state, is never logged, stored in browser storage, put in any URL again or rendered; the route sends `Referrer-Policy: no-referrer` and `Cache-Control: no-store` through `next.config.ts` headers; result states: pending ("Confirming your email address", announced politely), success (heading "Email verified", text that the account is ready, a link to the home page), `link_expired` (410), `link_used` (409), `link_invalid` (400) and a missing token, each with clear distinct wording and the request-a-new-link form below, and a network, timeout or `5xx` state with a "Try again" button that repeats the confirm request using the token still held in memory; the error code is read from the standard envelope (`error.code`), never guessed from text, and unknown codes fall back to the generic state; the request-a-new-link form is a single email field with a visible label, `type="email"`, `autocomplete="email"`, `noValidate`, posting `POST /api/v1/email-verification-requests` with body `{"email": "<address>"}`; on `202` it shows a generic confirmation ("If that address needs verification, we have sent a new link. Check your inbox and spam folder.") that never says whether the address exists; on `429` it shows "Too many attempts. Try again in N minutes." from `Retry-After` (minutes rounded up, singular "1 minute", "Try again later." when the header is absent or not numeric) and keeps the entered value; on `400` it shows the server's field message under the input and in a focused summary like the registration page; on network, timeout (about 20 seconds, `AbortController`) or `5xx` it shows a generic retry message and keeps the value; the button is disabled while pending with a ref guard against double submit; the frontend contains no business rules or token validation; the shared form helpers from the registration page (rate-limit message, envelope reader, timeout handling) move into one small module under `src/lib` and both pages use it, with the registration page's behaviour and tests unchanged; accessibility: focus moves to the result heading or notice when the state changes, live region announcements, visible focus, axe passes on every state at desktop and mobile widths; styles reuse the Atlas CSS variables; lint, type check, build, `api:check` and the whole Playwright suite (including the registration tests) pass; comments only where intent is not obvious.

**Never:** add sign-in, sessions, profile or other pages, add client-side validation rules beyond `required` semantics, log or persist the token or the email, call the backend directly from the browser, hand-edit `src/api/`, change the backend repository's code, or add a UI or form library.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid link | `/verify-email?token=T`, stub confirms | token removed from the URL, one confirm request with the token, success state "Email verified" | N/A |
| Expired | stub returns 410 `link_expired` | expired message and the new-link form | N/A |
| Used | stub returns 409 `link_used` | used or replaced message (suggest signing in later if already verified) and the new-link form | N/A |
| Invalid | stub returns 400 `link_invalid` | invalid message and the new-link form | N/A |
| Missing token | `/verify-email` with no token | no confirm request; invalid-link message and the form | N/A |
| Server or network error | 500, dropped connection or hang | generic retry state; "Try again" repeats the request with the same token held in memory | no detail shown |
| Single request | page loads and re-renders | exactly one confirm request | asserted |
| Token privacy | page lifecycle | token absent from the address bar after load, from storage, console output and rendered text | asserted |
| Headers | response for the page | `Referrer-Policy: no-referrer` and `Cache-Control: no-store` | asserted |
| Resend success | new-link form, stub returns 202 | generic confirmation, no existence hint | N/A |
| Resend limited | stub returns 429 with `Retry-After` 20, 90, absent, non-numeric | "1 minute", "2 minutes", "Try again later." twice; value kept | N/A |
| Resend field error | stub returns 400 with an email message | message under the input and in the focused summary | static server message |
| Resend failure | 500, network drop, timeout | generic retry message; value kept | N/A |
| Pending | delayed response | disabled button, announced pending state, no double submit | N/A |
| Accessibility | axe on pending, success, each failure and resend state, desktop and mobile | no violations | N/A |
| Shared module | registration page tests | still pass unchanged | N/A |
| Contract | `npm run api:check` | client current with the backend `main` contract | N/A |

</frozen-after-approval>

## Code Map

- `src/api/` -- regenerated by `npm run api:sync`
- `src/app/(public)/verify-email/page.tsx`, `verify-email-client.tsx`, `request-link-form.tsx` -- page, confirm flow and resend form
- `src/lib/forms.ts` (new) -- shared helpers moved out of `register-form.tsx`; `src/app/(public)/register/register-form.tsx` -- uses them; `src/lib/api.ts` -- reuse
- `next.config.ts` -- headers for `/verify-email`
- `tests/e2e/stub-backend.mjs`, `tests/e2e/verify-email.spec.ts` -- stub routes for confirm and resend with modes; tests for every matrix row, one axe test per state; `README.md` -- document the page

## Tasks & Acceptance

**Execution:**
- [ ] `npm run api:sync`; shared helper module
- [ ] verify page, confirm flow, resend form, headers
- [ ] stub routes and Playwright tests for every matrix row
- [ ] lint, type check, build, `api:check`, full suite, README

**Acceptance Criteria:**
- Given the frontend, when I follow a valid link, then a verification result page confirms my email and shows success.
- Given an expired, already-used or invalid link, when I open it, then I see a clear distinct error and can request a new link.
- Given repeated new-link requests beyond the limit, when the backend answers 429, then the page shows the limit message and keeps what I typed.
- Given the page, when it loads, then the token is not left in the address bar, never logged or stored, and the page sends no referrer.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Design Notes

Decisions made without asking: the page confirms automatically on load (the backend requires a POST so link scanners cannot consume the token); the token is cleared from the address bar as early as possible to limit leakage through history and screenshots; shared form helpers are extracted now because two pages use them; the success state links home because sign-in arrives in Story 1.17; wording distinguishes expired, used and invalid links because the backend now provides three codes.

## Verification

**Commands (frontend repository):**
- `npm ci && npm run api:sync && npm run lint && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` -- expected: all pass
