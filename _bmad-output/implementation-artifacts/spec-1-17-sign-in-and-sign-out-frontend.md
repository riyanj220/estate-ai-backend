---
title: 'Story 1.17: Sign-in and sign-out (frontend half)'
type: 'feature'
created: '2026-11-19'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-17-sign-in-and-sign-out.md'
  - '{project-root}/docs/sessions.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-16-email-verification-frontend.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The backend can authenticate sessions, but the frontend has no sign-in page, no sign-out, no knowledge of who is signed in, and the signed-in area is open to anyone.

**Approach:** In the frontend repository (`/home/riyan/Riyan/projects/estate-ai-frontend`, branch `story/1-17-sign-in-and-sign-out`) regenerate the client (already synced), add an in-memory CSRF helper, a session provider that restores the session through `GET /api/v1/me`, a site header that reflects the signed-in state with sign-out, a `/sign-in` page, and a server-side guard for the signed-in area. Functionality first, simple token-based Atlas styling, no business rules in the frontend (it only reads `state` and `capabilities`).

## Boundaries & Constraints

**Always:**
- Same-origin calls only, through the typed client (`getApi`) and the existing `/api/*` proxy. The session cookie is HttpOnly: no script reads, stores or sends it by hand. No token, password or email is written to storage, console or URL.
- **CSRF helper** (`src/lib/csrf.ts`): `getCsrfToken()` fetches `GET /api/v1/csrf` once and keeps the token in memory; `clearCsrfToken()` drops it; unsafe calls send it as `X-CSRFToken`. After a successful sign-in and after a sign-out the cached token is dropped and refetched lazily (the backend rotates it). If an unsafe call returns `403 forbidden`, refetch the token once and retry that call once; a second `403` is a generic failure. Concurrent callers share one in-flight fetch.
- **Session provider** (client component mounted in the root layout): on mount calls `GET /api/v1/me`; exposes `{status: "loading" | "signed_out" | "signed_in" | "error", account, signIn, signOut, refresh}`. `200` means signed in (account summary `{id, account_type, state, email, full_name, capabilities}` kept in memory only), `401` means signed out, anything else or a network failure is `error` (the header shows a neutral "Could not check your session" with a Retry, never a false signed-out claim). Requests use the shared `withTimeout`. No flash: while loading the header renders a stable placeholder.
- **Site header** (in the root layout, on every page): brand link to `/`; for a Visitor, links "Sign in" and "Create account"; for a signed-in account, the display name (`full_name`, else the email) and a "Sign out" button, plus a plain-text state note when `state` is `email_unverified` ("Email not verified") or `suspended` ("Account restricted"), driven only by `state`. Sign-out calls `DELETE /api/v1/session` with the CSRF token; on `204` or a `401` (already gone) the provider becomes signed out and the user lands on `/`; any other failure shows a polite error and stays signed in. Double clicks never send two requests.
- **Sign-in page** `/sign-in` (public): form with Email address and Password, both required, labelled, `autocomplete` `email` and `current-password`, same accessibility pattern as the register page (error summary with focus, `aria-live` status, busy state, no double submit). Submits `POST /api/v1/session` with the CSRF token; `200` makes the provider signed in and navigates to the validated `next` target or `/dashboard`; `401 invalid_credentials` shows ONE generic message ("The email or password is not correct.") and keeps the email, clears the password; `400 validation_error` field messages from the server envelope are shown against their field; `403` after the one CSRF retry, `5xx`, timeout and network failure show their own generic messages without leaking details; never any distinction between wrong password, unknown email, deactivated or locked out. A signed-in visitor opening `/sign-in` is redirected to `/dashboard`. The page links to "Create an account".
- **`next` parameter**: only a relative path starting with a single `/` (not `//`, not `/\`, no scheme, no control characters, length at most 200) is honoured; anything else falls back to `/dashboard`. The guard builds `?next=` itself from the requested path.
- **Guard for the signed-in area** (`(app)` group, server side): the layout asks the backend `GET /api/v1/me` with the forwarded cookie through the server `getApi`; a `401` redirects to `/sign-in?next=<requested path>`; a backend failure renders a neutral "temporarily unavailable" state, never the protected content; a signed-in render is not cached (`Cache-Control: no-store`, dynamic rendering). The dashboard placeholder shows the display name and state from the account. The admin area keeps its placeholder but is also guarded the same way (any signed-in account may see the placeholder until Story 1.19; it shows nothing sensitive).
- Existing pages: the verification success state links "Sign in" to `/sign-in` and the `link_used` wording drops "once sign-in is available"; the register success state links to `/sign-in`; the home page links to sign-in. README documents the pieces, the stub modes and the hand-off to Story 1.18 (forgot-password link goes on the sign-in page then).
- Tests (Playwright against the stub backend, which gains: `/api/v1/csrf`, `/api/v1/session` POST and DELETE, `/api/v1/me`, with modes for `ok`, `unverified`, `suspended`, `wrong_credentials`, `validation`, `csrf_once` (first unsafe call 403 then ok), `csrf_always`, `error`, `broken`, `hang`, and a stateful cookie so `me` is 401 before sign-in and after sign-out) cover every matrix row; axe on the sign-in states at desktop and mobile widths; existing suites keep passing. Lint, typecheck, build and `api:check` pass.

**Never:** put the CSRF token, password, email or session data in storage, the URL or logs; read the session cookie in script; decide permissions in the frontend beyond showing `state` and `capabilities`; show different messages for different credential failures; follow an unvalidated `next`; render protected content when the backend check failed; add a dependency without need; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Visitor header | `me` 401 | "Sign in" and "Create account" links | N/A |
| Restore session | `me` 200 | header shows name, "Sign out"; state note for unverified or suspended | N/A |
| Session check fails | `me` 500, timeout or network | neutral message with Retry; not shown as signed out | retry button |
| Sign in ok | valid credentials | provider signed in, navigates to `/dashboard` (or valid `next`), header updates | N/A |
| Bad credentials | 401 `invalid_credentials` | one generic message, email kept, password cleared, focus to the message | N/A |
| Server field errors | 400 with fields | messages against fields, summary, focus | N/A |
| CSRF self-heal | first unsafe call 403 | token refetched, call retried once, succeeds | second 403 is a generic failure |
| Failure kinds | 5xx, timeout, network | separate generic messages, no detail leak | N/A |
| Double submit | click twice | exactly one request | N/A |
| Already signed in | open `/sign-in` | redirect to `/dashboard` | N/A |
| `next` | `/dashboard`, `//evil.com`, `https://x`, `/\\x`, long, control chars | only the first is honoured; others go to `/dashboard` | N/A |
| Guard | Visitor opens `/dashboard` | redirect to `/sign-in?next=%2Fdashboard`; no protected content flashes | N/A |
| Guard backend down | `me` 500 on the server | neutral unavailable state, no content | N/A |
| Sign out | click | `DELETE` with token; `204`; header returns to Visitor; `/dashboard` is then guarded; token refetched next time | N/A |
| Sign out 401 | session already gone | treated as signed out | N/A |
| Sign out failure | 500 | polite error, still signed in | N/A |
| Unverified | `state` email_unverified | signed in with the "Email not verified" note | N/A |
| Privacy | after sign-in | no password, email or token in storage, console, URL or rendered attributes outside the header and form | test |
| Accessibility | sign-in states | axe clean at desktop and mobile | test |

</frozen-after-approval>

## Code Map

- `src/lib/csrf.ts`, `src/lib/session.tsx` (provider and hook), `src/lib/next-target.ts` (validation)
- `src/app/layout.tsx`, `src/components/site-header.tsx`
- `src/app/(public)/sign-in/page.tsx`, `sign-in-form.tsx`
- `src/app/(app)/layout.tsx`, `src/app/(app)/dashboard/page.tsx`, `src/app/admin/layout.tsx` -- guard
- small wording and link updates in verify-email, register and home pages
- `tests/e2e/stub-backend.mjs`, `tests/e2e/sign-in.spec.ts`, README

## Tasks & Acceptance

**Execution:**
- [ ] csrf helper, session provider, header
- [ ] sign-in page, `next` validation
- [ ] server-side guard for the signed-in and admin areas
- [ ] stub backend modes and Playwright tests; README

**Acceptance Criteria:**
- Given the sign-in and sign-out screens, when I use them, then CSRF handling works through the proxy and the signed-in state is reflected in the layout.
- Given wrong credentials, when I sign in, then I see one generic message that does not reveal why.
- Given I am signed out, when I open the signed-in area, then I am sent to sign in and returned afterwards.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, frontend half (blind, edge-case, verification-gap).

**Patched:**
- Back after sign-out could redisplay the cached dashboard: sign-out now does a hard navigation.
- Header went stale (expiry, other tab): the provider re-checks silently on focus and route change; a 401 flips to signed out.
- CSRF fetch failures and sign-in 429 were mislabelled as network errors: mapped to server and rate-limit messages; a 200 with an unparsable body is still a success.
- Sign-in form could stay disabled after a stalled navigation: pageshow reset and a safety timer; empty fields validated on the client; one announcement mechanism; show/hide label.
- Guard hardening: every guarded page calls `requireAccount` itself, the proxy always overwrites `x-requested-path` for all page routes and drops `_rsc`, `next` rejects `/api` and `/_next`, a signed-in visit to `/sign-in` honours a valid `next` (deviation from the frozen text, a safe improvement).
- `csrf.ts` is client-only; the `Account` type and state labels are defined once.
- Tests added (21): exact network message, 429, CSRF fetch failure, empty-name fallback, sign-out failure kinds, shared CSRF fetch, query preserved through the guard, spoofed header, direct admin visit, register-success link, Back after sign-out, `next` boundaries, empty submit, focus revalidation.

**Rejected:** retry on any 403 double-counting the sign-in throttle (the backend CSRF check rejects before the service, comment added).

**Deferred:** `next` length boundary tests, extra Cache-Control matrix, axe for the remaining error states, fetching `/me` only on pages that need it, role check for the admin area (Story 1.19), email shown nowhere beyond the header (no finding).

## Design Notes

Decisions made without asking: the session provider lives in the root layout and restores state through `me` so the header is correct on every page; protected pages are guarded on the server so nothing flashes; the CSRF token lives only in memory and self-heals once on a 403 because the backend rotates it at sign-in and sign-out; the frontend never distinguishes credential failures; `next` is validated to same-origin relative paths to avoid open redirects.

## Verification

**Commands (frontend repository):**
- `npm run api:sync && npm run lint && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` -- expected: all pass
