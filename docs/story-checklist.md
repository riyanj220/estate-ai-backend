# Story checklist (from the Epic 1 retrospective)

Put these in every story spec's constraints and in its reviewers' briefs. Each item comes from a defect a reviewer found late in Epic 1 (sources in `_bmad-output/implementation-artifacts/epic-1-retro-2026-11-20.md`).

## Account existence side channels (any route or flow that touches an email address or account)

For every response branch (unknown, existing, unverified, verified, suspended, deactivated, administrator, capped, throttled, same-as-current):

- Status, body and headers are identical.
- The response path does the same kind of work (same lookups, same counter writes, same password hash call).
- Side effects an owner can observe are identical too: the mail they receive in their own inbox, profile fields such as a pending address, audit and notification records.
- Throttles use a silent identical answer, not a distinct status, wording or speed (run the dummy hash on the throttled branch).

## After-commit effects

- Mail, notifications and events fire after commit and are the last statement in their transaction block, so a later swallowed error cannot leave an effect for a flow that failed; add a rollback test for each.
- Anything that must not be lost (ending sessions, voiding tokens) happens inside the transaction, not only in an after-commit handler.

## Emailed-token pages (frontend)

- Statically prerendered, never `force-dynamic`; `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.
- The token is read once, removed from the address bar and from router `history.state` (`src/lib/scrub-token.ts`), and held only in memory.
- Nothing is sent on load; the person presses a button; Back and Forward tests assert neither the URL nor `history.state` contains the token.

## Admin and guarded areas

- Guard on the server (layout and every page); a Visitor, a non-administrator and an expired administrator are indistinguishable; nothing admin-related in redirect bodies, titles or cached or restored pages (bfcache).

## Tests

- Assert on the element that owns a text, never page-wide `getByText` (duplicate sr-only live regions made the agency-profile suite flaky).
- Dev-mode responses prove nothing about caching or prerender: check production behaviour with a build (`npm run check:prod-admin` is the model).
- Test fixtures: low-entropy fake-word passwords only (gitleaks).
