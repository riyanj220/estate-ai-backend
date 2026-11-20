---
title: 'Story 1.23: Admin area shell (frontend)'
type: 'feature'
created: '2026-11-20'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-22-administrator-provisioning.md'
  - '{project-root}/docs/administrators.md'
  - '{project-root}/docs/sessions.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-17-sign-in-and-sign-out-frontend.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The admin area is a placeholder open to any signed-in account, administrators have no sign-in page, and there is no shell for later administration screens to live in.

**Approach:** In the frontend repository (`/home/riyan/Riyan/projects/estate-ai-frontend`, branch `story/1-23-admin-area-shell`; the client is already synced) build the protected admin area: a separate admin sign-in page, a server-side administrator guard based on `GET /api/v1/admin/me`, and an empty dashboard shell with navigation placeholders, sign-out and change-password. The frontend decides nothing about administration: it shows what the backend allows and contains no administration business logic. Functionality first, simple token-based Atlas styling.

## Boundaries & Constraints

**Always:**
- **Route structure**: `/admin/sign-in` is public and outside the guarded group; everything else under `/admin` lives in a guarded route group (`src/app/admin/(protected)/...`). Keep `no-store` for `/admin/:path*` (already configured) and make sure `/admin/sign-in` is covered too.
- **Administrator guard** (server side, new `requireAdmin()` in `src/lib/current-account.ts` or a sibling, memoised per render like `checkAccount`): calls `GET /api/v1/admin/me` through the server `getApi` with the forwarded cookie. `200` is an administrator; `401` and `404` (Visitor, non-administrator, expired or disabled administrator) redirect to `/admin/sign-in?next=<requested /admin path>` with NO distinction and no admin content rendered or fetched; any other answer or a failure renders a neutral "temporarily unavailable" state with no protected content. Every guarded admin page calls `requireAdmin()` itself as well as the group layout (layouts are not re-run on client navigation). The existing `requireAccount()` is unchanged for the other signed-in pages. The old placeholder admin layout and page are replaced.
- **`next` handling**: the sign-in target is validated with `safeNext` AND must be `/admin` or start with `/admin/` (not `/admin/sign-in`); anything else becomes `/admin`.
- **Admin sign-in page** `/admin/sign-in` (public, `method="post"` form, `no-store`): fields Email address and Password (`autocomplete` `username`/`email` and `current-password`, show/hide control), same accessibility pattern as the ordinary sign-in form (labelled fields, error summary with focus, one announcement mechanism, busy state, no double submit, client required checks send nothing). Submit `POST /api/v1/admin-session` through `withCsrf`; `200` refreshes the session provider and navigates to the validated target; `401 invalid_credentials` shows ONE generic message ("The email or password is not correct.") whatever the cause (never any hint that an address is or is not an administrator, never any lockout wording), keeps the email, clears the password; `400` field errors mapped from the server envelope; 429 (not expected), 403 after the one CSRF retry, 5xx, timeout, network show their own generic messages. A visitor, a signed-in non-administrator and an expired administrator all see the form; an administrator already signed in (server check through `/api/v1/admin/me`) is redirected to the validated target or `/admin`. The page does not link to the public sign-in and the public pages do not link to it. Heading text identifies it as "Administrator sign in".
- **Shell** (the guarded layout): its own admin chrome instead of the public header: product name "EstateAI Administration", a primary navigation with placeholder entries (Dashboard, Listings, Users, Reports, Audit log) where only Dashboard is a link (current page marked with `aria-current="page"`) and the others are rendered as disabled items labelled "Coming soon" (not links, not focusable as links, and no routes behind them), a "Change password" link to the existing `/account/password` page, and a "Sign out" button; landmark structure (`header`, `nav` with an accessible name, `main`), skip link. The root layout's public header is not shown on admin pages (make the public site header hide itself under `/admin`, or move it into the public route groups; do not duplicate its code). Sign-out uses the existing provider `signOut` (CSRF, `DELETE /api/v1/session`) and then does a hard navigation to `/admin/sign-in`, like the public sign-out.
- **Dashboard** `/admin`: heading "Administration", the administrator's state from `/api/v1/admin/me` as plain text, and an empty-state message ("Administration tools will appear here.") plus the placeholder navigation; nothing else. No administration business logic, no backend calls other than `/api/v1/admin/me`.
- **Session expiry**: administrator sessions end after 30 minutes idle or 8 hours (backend). A client component in the shell watches the session provider (already re-checks on focus, visibility and route change) and, when the provider becomes signed out or reports the account is not an administrator, sends the person to `/admin/sign-in?next=<current admin path>` without rendering further admin content; a 401 or 404 on the next guarded navigation does the same through the server guard.
- **Admin password change**: the existing `/account/password` page keeps working for administrators; add the three administrator policy codes (`admin_password_too_short`, `admin_password_weak`, `admin_password_whitespace`) to the shared password message map with human text ("Administrator passwords need at least 12 characters.", "Administrator passwords need at least one letter and one digit.", "Administrator passwords cannot start or end with a space."); the change-password page for an administrator returns to the admin area link instead of the public home link where it shows one. The public profile page and header links stay hidden for administrators (already so).
- **Privacy and caching**: no administrator data in storage, console or URL; the admin pages and the sign-in page send `Cache-Control: no-store`; a non-administrator request to any `/admin` page never receives admin markup (verify with a production build: `next build && next start` against the stub backend, curl as a Visitor and as an ordinary account: `307` to `/admin/sign-in` with no admin content in the body).
- Tests (Playwright against the stub backend, which gains `POST /api/v1/admin-session`, `GET /api/v1/admin/me` (401 for visitors, 404 for non-administrators, 200 for an administrator session) and modes for the matrix rows, reusing the stateful session and CSRF stub and its expiry helpers) cover every matrix row; axe at desktop and mobile on the main states; existing suites keep passing (the old admin placeholder tests change as described); lint with `--max-warnings 0`, typecheck, build and `api:check` pass; README documents the admin area, the stub modes and the hand-off to later admin screens. IMPORTANT: gitleaks flags password-looking high-entropy literals: use only low-entropy fake-word passphrases ("correct horse battery staple 42"), no random-looking strings or token-like literals.

**Never:** render or fetch any admin content before the guard confirms an administrator; distinguish Visitor from non-administrator from expired administrator in redirects, messages or timing of the UI; show lockout, throttle or "not an administrator" wording; link to the admin sign-in from public pages; put administration business rules in the frontend (it shows the backend's answers only); follow a `next` outside `/admin`; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Visitor opens `/admin` | no session | 307 to `/admin/sign-in?next=%2Fadmin`; no admin content | N/A |
| Ordinary user opens `/admin` | Individual or Agency session | same redirect (404 from the backend treated like 401); no admin content | N/A |
| Administrator opens `/admin` | admin session | shell with navigation placeholders and the dashboard empty state | N/A |
| Guard, backend down | `/admin/me` 5xx or timeout | neutral unavailable state, no admin content | N/A |
| Deep path | `/admin/anything` (guarded group) | same guard; `next` keeps the path only if it is under `/admin` | N/A |
| Sign in ok | valid administrator credentials | session provider refreshed, navigates to `/admin` or the validated `next` | N/A |
| Sign in, any failure of credentials | wrong password, unknown email, ordinary account, disabled admin, throttled | ONE generic message, email kept, password cleared, focus on the message | N/A |
| Sign in, other failures | 400 fields, 403, 5xx, timeout, network | mapped or generic messages without detail leaks | N/A |
| CSRF self-heal | first unsafe call 403 | token refetched, retried once | second 403 generic |
| Empty fields | blank email or password | client required errors, no request | N/A |
| Double submit | click twice | exactly one request | N/A |
| Already an administrator | open `/admin/sign-in` while signed in as admin | redirect to `/admin` (or valid `next`) | N/A |
| `next` validation | `/admin/x`, `/dashboard`, `//evil.com`, `/admin/sign-in`, `https://x` | only `/admin/x` honoured; others go to `/admin` | N/A |
| Public pages | home, sign-in, register | no link to the admin sign-in | test |
| Navigation | shell nav | only Dashboard is a link, others are disabled "Coming soon" items; `aria-current` on Dashboard | N/A |
| Sign out | click | `DELETE` with token; hard navigation to `/admin/sign-in`; `/admin` is guarded again | N/A |
| Idle or absolute expiry | stub expires the session | next guarded navigation or the provider check sends the person to `/admin/sign-in?next=...` without admin content | N/A |
| Admin password change | weak new password | the three admin codes shown as human text | N/A |
| Public header | any `/admin` page | public header absent; admin chrome present | N/A |
| Headers and prerender | production build | `/admin`, `/admin/sign-in` served `no-store`; Visitor and ordinary-account requests to `/admin` get 307 with no admin markup | manual curl check documented |
| Privacy | after any state | no credentials or admin data in storage, console or URL | test |
| Accessibility | sign-in and shell states | axe clean at desktop and mobile; landmarks, skip link, nav name | test |

</frozen-after-approval>

## Code Map

- `src/lib/current-account.ts` (`requireAdmin`), `src/proxy.ts` (unchanged unless needed), `next.config.ts` (headers)
- `src/app/admin/layout.tsx` (minimal), `src/app/admin/sign-in/{page,admin-sign-in-form}.tsx`, `src/app/admin/(protected)/{layout,page}.tsx`, `admin-shell.tsx`, `admin-session-watch.tsx`
- `src/components/site-header.tsx` (hide under `/admin`) or route-group relocation, `src/lib/password-messages.ts` (admin codes), `src/app/(app)/account/password/*` (admin return link)
- `tests/e2e/stub-backend.mjs`, `tests/e2e/admin.spec.ts`, README

## Tasks & Acceptance

**Execution:**
- [ ] `requireAdmin` guard and the protected route group with the shell
- [ ] admin sign-in page and form
- [ ] session expiry watcher, public header hidden, admin password codes and return link
- [ ] stub modes, Playwright tests, production check, README

**Acceptance Criteria:**
- Given the admin route group, when a Visitor or a non-administrator opens it, then they are sent to the admin sign-in page and cannot see any admin content.
- Given an administrator, when they sign in, then an empty dashboard shell with navigation placeholders is shown and the accessibility check passes, and the frontend contains no administration business logic.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (blind, edge-case, verification-gap).

**Patched:**
- A page restored from the browser's back/forward cache after sign-out could show the admin dashboard to the next person: `pageshow` with `persisted` resets the pause and reloads through the guard; real Back and simulated persisted tests added.
- The admin door distinguished credential failures: 403 and 429 showed different wording from a wrong password; all three now show the one generic message (no lockout or rate-limit wording).
- Shared server fetch timeout (`src/lib/api.ts`, outside the spec): the timer was never cleared and the caller's signal was dropped; now cleared on settle, signals combined, timeout errors recognised; slow-success and hang tests added.
- An expired administrator on the shared `/account/password` page landed on the ordinary sign-in (a dead end) and saw the public header: the page now routes an administrator back to the admin sign-in and shows the admin chrome.
- `/admin/me` 403 treated as not an administrator, an admin unavailable state with Sign out and a working retry, a watcher that does not treat a missing `account_type` or a still-loading provider as lost, a slow post-sign-in navigation re-checks the session.
- Guard rule enforced by a test that every page under `(protected)` calls `requireAdmin`, layout `noindex` and a neutral title, valid disabled-nav markup, sign-out keeps focus.
- Tests: page-level guard independent of the watcher (`denied_*` modes), hard-navigation marker, public header absent on 404 and unavailable states, watcher keeps deep path and query, and an automated production-build check (`npm run check:prod-admin`, run once: visitor and ordinary cookie get 307 with no admin markup and no-store).

**Deferred:** a single page data-access helper (the enumeration test enforces the rule instead), fail-closed after repeated provider errors, the shared `estate_session` cookie between admin and ordinary sessions (backend follow-up), per-page titles beyond the neutral default.

Notes: one known flaky agency-profile test failed once in a full run and passed alone and in its whole file; the final full run was 572 of 572. Intermediate commits keep the old admin placeholder page until the last commit removes it (history only; HEAD builds).

## Design Notes

Decisions made without asking: the guard asks the dedicated `/api/v1/admin/me` rather than the ordinary `/me` so the backend alone decides who is an administrator, and its 401 and 404 answers are treated alike so nothing about the admin area is confirmed to non-administrators; the admin sign-in page is deliberately not linked from public pages; navigation placeholders are inert text rather than links to avoid shipping empty routes; the public site header is hidden under `/admin` so the admin area has one clear chrome; an expired administrator session sends the person back to the admin sign-in with their place preserved.

## Verification

**Commands (frontend repository):**
- `npm run api:sync && npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` -- expected: all pass (the full Playwright run takes about 14 minutes: run it in the background with output to a file; never run two Playwright runs at once)
