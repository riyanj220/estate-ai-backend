---
title: 'Story 1.24: In-app notification list (frontend half)'
type: 'feature'
created: '2026-11-20'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-24-in-app-notification-list.md'
  - '{project-root}/docs/notifications.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-21-agency-business-profile-frontend.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The backend stores notifications for account holders, but there is no page to read them, no unread indicator, and no way to mark them read.

**Approach:** In the frontend repository (`/home/riyan/Riyan/projects/estate-ai-frontend`, branch `story/1-24-in-app-notification-list`; the client is already synced) add a signed-in `/account/notifications` page (the list, an unread count, load-more with the cursor, mark one read, mark all read, an unread filter) and a header link with an unread count for every signed-in account (individual, agency and administrator). The frontend only renders what the backend returns: kinds map to human text in one place, unknown kinds fall back to a generic line, and nothing about notification content or privacy is decided here. Functionality first, simple token-based Atlas styling. New Messages are not notifications (Epic 7) and there is no email alert UI.

## Boundaries & Constraints

**Always:**
- Same-origin typed client calls through `getApi` and the proxy; reuse `withTimeout`, `readErrorBody`, `rateLimitMessage`, `withCsrf` for every unsafe call, the shared hooks and patterns (`useProfileLoad`-style loading with Retry kept mounted, `useFormFeedback`-style single announcement mechanism, `sessionGone` handling), and the accessibility conventions of the profile pages. No notification content, id or count goes to storage, console or URL.
- **Kind catalogue** (`src/lib/notification-kinds.ts`): a typed map from kind to `{ title, text }` (human English; a function of the payload only where the kind has payload keys). Ship `account.password_changed` ("Your password was changed.", detail "If this was not you, reset your password and contact support.") and `account.email_changed` ("Your sign-in email address was changed.", detail "If this was not you, contact support."). An unknown kind renders "You have a notification." with no payload shown; payload values are never rendered raw for unknown kinds, and for known kinds only the fields the catalogue entry names, as plain text (never HTML). Later epics add entries here.
- **Page** `/account/notifications` (signed-in area, guarded with `requireAccount`, `Cache-Control: no-store` via `next.config.ts`, available to all account types): heading "Notifications"; loads `GET /api/v1/notifications` (default page) on the client with loading, error with Retry (kept mounted) and a `401` that flips the provider to signed out and goes to `/sign-in?next=%2Faccount%2Fnotifications` (administrators go to `/admin/sign-in?next=%2Fadmin`). Each item shows the title, the detail text, a relative or absolute created time (use a stable, accessible `<time datetime>` with a readable local format), an "Unread" marker (text, not colour alone) and a "Mark as read" button for unread items. **Filter**: "All" and "Unread" as a pair of toggle buttons (`aria-pressed`) that reload from the server with `state=all|unread` (resetting the cursor). **Load more**: a "Load more" button follows the server `next` link (cursor) and appends without duplicates (dedupe by id), hidden when there is no `next`; an error loading more keeps the existing list and shows a retry message. **Mark read**: `POST /api/v1/notifications/{id}/read` through `withCsrf`; on `200` the item becomes read in place (in the Unread filter it is removed from the list), the header unread count refreshes; a `404` (gone) removes the item quietly and refreshes the count; other failures show a polite per-page message and keep the item unread. **Mark all read**: a button (disabled when the unread count is zero) calling `POST /api/v1/notifications/read-all` through `withCsrf`, then reloads the list from the server and refreshes the count; shows "N notifications marked as read." (or "No unread notifications.") in the single live region. **Empty state**: "You have no notifications." (or "You have no unread notifications." under the Unread filter). Double clicks never send duplicate requests; the live region announces results once; focus moves sensibly (after mark-read removes an item from the Unread filter, focus goes to the list heading or the next item, not the body).
- **Header link and unread count**: for signed-in accounts the shared site header and the admin chrome show a "Notifications" link to `/account/notifications` (hidden on admin-only edge cases only if the page is unavailable to them: it is available to all types) with the unread count as visible text and in the accessible name when greater than zero ("Notifications, 3 unread"; cap the display at "99+"). A small shared hook `useUnreadCount()` in the session layer loads `GET /api/v1/notifications/unread-count` once when the session becomes signed in, refreshes on window focus and visibility change (silent, never flipping to a loading state), after any mark-read or mark-all-read on the page, and clears when signed out; a failure keeps the last known count and shows no error (the count is a hint, the page is the truth). The unread count never blocks rendering and is never shown to Visitors. No polling timer beyond focus and visibility.
- **Privacy and caching**: the page and the count endpoint calls are never cached (`no-store`); unread counts are not placed in the document title, URL or storage.
- Tests (Playwright against the stub backend, which gains `GET /api/v1/notifications` (cursor pagination with `next` links, `state` filter, 20 per page), `GET /api/v1/notifications/unread-count`, `POST /api/v1/notifications/{id}/read`, `POST /api/v1/notifications/read-all`, modes for the matrix rows, seeded with notifications of both known kinds and an unknown kind, stateful so marking read changes the list and the count, and reusing the session and CSRF stub) cover every matrix row; axe at desktop and mobile on the main states; existing suites keep passing (header tests change for the new link); lint with `--max-warnings 0`, typecheck, build and `api:check` pass; README documents the page, the kind catalogue and how later epics add kinds, the stub modes and the end-of-Epic-1 hand-off. IMPORTANT: gitleaks flags password-looking high-entropy literals: use only low-entropy fake-word values in tests and the stub, no random-looking strings or token-like literals (notification ids may be repetitive digit patterns).

**Never:** render payload values of unknown kinds or any raw HTML from a payload; show another account's data (the backend filters; the frontend never requests by another account id); poll on a timer; show the unread count to Visitors or put it in the URL, title or storage; block the page on the count; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Open page | signed-in account with notifications | list newest first with title, detail, time and unread markers; heading focus | N/A |
| Empty | no notifications | "You have no notifications." | N/A |
| Load fails | 5xx, timeout, network | message with Retry (kept mounted) | N/A |
| Session gone | 401 | signed out and redirected to the right sign-in with `next`; administrators to the admin sign-in | N/A |
| Guard | Visitor opens the page | redirect to sign-in with `next`; no content | N/A |
| Unknown kind | server sends an unregistered kind | "You have a notification." and no payload shown | N/A |
| Known kinds | password and email changed | catalogue title and detail | N/A |
| Filter unread | click Unread | server reload with `state=unread`; empty state wording for unread | N/A |
| Load more | `next` link present | appends the next page without duplicates; button hidden at the end | error keeps list, retry message |
| Mark one read | unread item | item read in place (removed under Unread); header count decreases | N/A |
| Mark gone item | 404 | item removed quietly, count refreshed | N/A |
| Mark failure | 5xx, timeout, network, second 403 | polite message, item stays unread | N/A |
| CSRF self-heal | first POST 403 | token refetched, retried once | second 403 generic |
| Mark all read | unread items exist | all read; list reloaded; count zero; announcement with the number | N/A |
| Mark all, none unread | count zero | button disabled | N/A |
| Double click | click twice | exactly one request | N/A |
| Header count | unread above zero | link name "Notifications, N unread"; "99+" cap; hidden count at zero | N/A |
| Header refresh | focus or visibility change, after mark read | count re-read silently; a failure keeps the last count | N/A |
| Visitor | signed out | no Notifications link, no count request | N/A |
| Account types | individual, agency, administrator | link and page available; administrator sees it in the admin chrome | N/A |
| Privacy | after any state | no ids, content or counts in storage, console, URL or title | test |
| Accessibility | list, empty, error, filter states | axe clean at desktop and mobile; live region announces once | test |

</frozen-after-approval>

## Code Map

- `src/app/(app)/account/notifications/{page,notifications-client}.tsx`
- `src/lib/notification-kinds.ts`, `src/lib/use-unread-count.ts(x)` (session-level hook), `src/components/site-header.tsx`, `src/app/admin/(protected)/admin-shell.tsx` (link), `next.config.ts` (headers if needed)
- `tests/e2e/stub-backend.mjs`, `tests/e2e/notifications.spec.ts`, README

## Tasks & Acceptance

**Execution:**
- [ ] kind catalogue and the unread-count hook
- [ ] notifications page: list, filter, load more, mark read, mark all read
- [ ] header and admin chrome link with the count
- [ ] stub modes, Playwright tests, README

**Acceptance Criteria:**
- Given the notifications page, when I open it, then the list and the unread count show, and I can mark notifications read.
- Given new Messages, when they arrive, then they are not notifications (documented non-goal), and no email alert UI exists.
- Given later epics, when they add notification kinds, then they add one catalogue entry in one place.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, frontend half (blind, edge-case, verification-gap).

**Patched:**
- The list and the header count were separate sources of truth: "Mark all as read" was disabled by a stale zero count while unread items showed, and could replace loaded pages with page 1. The loaded list now decides button states, mark-all marks loaded items in place and keeps loaded pages, a disabled click announces "No unread notifications."
- Filter and mutation races: first-page fetches are tagged with their filter and dropped when stale, mark and load-more actions are refused while a reload is in flight, a mutation that raced a reload triggers a quiet re-read, mark-one after the list changed only refreshes the count, and the focus target cannot leak.
- Unread filter emptied with a `next` link auto-fetches pages (bounded) instead of showing a blank list with Load more.
- Parsing and errors: one malformed item no longer fails the page, an unusable `next` is a load error with Retry, duplicate-only load-more is announced and ends the list, list 429 shows the wait and 403 a clear message without a Retry loop.
- Unread count provider: keyed on the account id (a count owned by another account is never shown), focus and visibility coalesced with a 5 second minimum interval, 403 and 429 stop reads until the next sign-in, a failed first read retries once.
- Accessibility: list items instead of a heading per item, `aria-current` on the header link, busy text on Load more, identical announcements re-announce.
- Tests (66 new): hung requests, no-store request proof, exact count reads, sign-in and account-switch count behaviour, 401 on every action, network aborts, out-of-order toggling, stale zero count, mark-all keeps pages, double-click on Load more, announce-once, extra payload keys and prototype-key kinds, privacy including load-more.

**Deferred:** a dedicated list hook instead of stretching `useProfileLoad` (README tech debt), scroll anchor restoration after re-reads, a page-level count retry (it would break the exact-once count read), e2e tests for the empty-title fallback and the unusable load-more body.

Notes: `agency-profile.spec.ts` (a serial file from Story 1.21) failed once in each of three full runs on a different cities test and passes in full alone (82 of 82); recorded as test tech debt (the serial mode skips the rest of the file after one flake).

## Design Notes

Decisions made without asking: kinds are rendered from a frontend catalogue keyed by the backend kind so copy stays in the frontend while the backend stays the source of truth for what happened; unknown kinds degrade to a generic line without showing payload values; the unread count is a best-effort hint refreshed on focus and visibility instead of a timer, matching the backend's guidance (refetch page one and the count rather than rely on cursor-forward polling); the Unread filter removes items as they are read so the view stays honest; the same page and header link serve every account type.

## Verification

**Commands (frontend repository):**
- `npm run api:sync && npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` -- expected: all pass (the full Playwright run takes about 13 minutes: run it in the background with output to a file; never run two Playwright runs at once)
