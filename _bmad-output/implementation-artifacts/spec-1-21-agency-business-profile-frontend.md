---
title: 'Story 1.21: Agency business profile (frontend half)'
type: 'feature'
created: '2026-11-20'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-21-agency-business-profile.md'
  - '{project-root}/docs/profile.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-20-individual-profile-management-frontend.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-19-agency-registration-frontend.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The backend lets an Agency account read and edit its business profile, but the frontend has no page for it, and the existing profile page and its header link only serve Individuals.

**Approach:** In the frontend repository (`/home/riyan/Riyan/projects/estate-ai-frontend`, branch `story/1-21-agency-business-profile`; the client is already synced) make `/account/profile` account-type aware: Individuals keep the existing page, Agency accounts see a business profile page (agency name, description, city, business phone, office address, plus the existing email-change form and a read-only verification status). Reuse the Story 1.20 profile code and the Story 1.19 city loader instead of copying them. Functionality first, simple token-based Atlas styling, no business rules in the frontend (server-driven validation; the frontend shows server answers and codes). Logo upload is out of scope (Epic 2).

## Boundaries & Constraints

**Always:**
- **One route, two bodies**: `/account/profile` keeps its guard (`requireAccount`) and `no-store`. The page decides by the session's `account_type`: `individual` renders the existing profile client unchanged; `agency` renders the new business profile client; `admin` shows the polite "Profile editing is available for individual and agency accounts." message with no form. The header and dashboard "Profile" links show for individual and agency accounts (agency label "Business profile"), not for admins. Unknown types behave like admin.
- **Business profile client** loads `GET /api/v1/me/agency-profile` on the client (`{agency_name, description, city_id, business_phone, office_address, email, pending_email, verification_status}`) with loading and Retry states (Retry keeps its button mounted, focus to the heading after success), silent re-read on focus and visibility, a `403` shows the polite wrong-account message, a `401` flips the session provider to signed out and goes to `/sign-in?next=%2Faccount%2Fprofile`. Shows the verification status as plain read-only text ("Agency verification: Unverified") driven only by the server value.
- **Details form** (agency name, description, city, business phone, office address): all mandatory except description; prefilled from the server; submit `PATCH /api/v1/me/agency-profile` through `withCsrf` sending only the fields that changed (nothing changed: no request, "No changes to save."); description is a textarea (1000 character limit, collapses newlines server-side; show a live character count text that is not announced on every keystroke), office address a textarea (300 limit, newlines collapse to a space on change like the registration form), city a `<select>` loaded from `GET /api/v1/reference/cities` with the Story 1.19 loader (extract the loader into a shared hook or component used by both the registration form and this page; the stored city is preselected; if the stored city is not in the active list show it as a disabled "Current city (no longer available)" option so the form still saves other edits, and only a changed city is sent). `200` shows "Your business profile was saved.", refreshes the form baseline from the response only for untouched fields (typed edits survive an in-flight save), and refreshes the session provider (the header agency name updates); `400` field errors (`required`, `invalid`, `invalid_name`, `invalid_phone`, `invalid_address`, `invalid_city`, `invalid_description`, `contains_contact_details`, `unknown_field`/non-field) are mapped to human text with the error summary focused (the `contains_contact_details` text explains that descriptions cannot contain phone numbers, email addresses or street addresses and that the business phone and address are kept private); `invalid_city` clears the selection and re-fetches the cities; known codes only, unknown codes, prototype keys and unknown field keys become generic text (use `mapKnownCodes`); 403 after the one CSRF retry, 429, 5xx, timeout and network show their own messages; success clears on edit; a 2xx with an unreadable body triggers a re-read and a polite note.
- **Privacy note on the page**: short static text beside the phone and address fields: "Your business phone and office address are private. Only your agency name, description and city are shown publicly." The private values appear only inside the form inputs, never in the header, dashboard or elsewhere.
- **Email section**: reuse the Story 1.20 email-change form and pending note unchanged (it already posts `{new_email, current_password}` and shows the neutral confirmation); the profile client passes it `email` and `pending_email` and re-reads the business profile after an accepted request. The emailed `/confirm-email-change` page already works for agencies.
- Shared code: extract what is genuinely shared with the Individual profile (the loader skeleton, `sessionGone`, silent re-read hooks, success/notice handling) only where the Individual client would otherwise be copied verbatim, keeping the Individual page's behaviour and tests unchanged; add `AGENCY_PROFILE_MESSAGES` next to the existing message maps.
- Tests (Playwright against the stub backend, which gains `/api/v1/me/agency-profile` GET and PATCH with modes for the matrix rows, reusing the stateful session, CSRF, cities and email-change stubs; an agency `/me` mode already exists) cover every matrix row; axe at desktop and mobile on the main states; existing suites keep passing (including the Individual profile tests and the header-link tests for agency and admin accounts, which change as described); lint, typecheck, build and `api:check` pass; README documents the page, the stub modes and the hand-off to Story 1.22. IMPORTANT: gitleaks flags password-looking high-entropy literals: use only low-entropy fake-word passphrases ("correct horse battery staple"), no random-looking strings or new token-like literals.

**Never:** show the business phone or office address outside the form inputs; send unchanged fields; offer an editable email or verification status on this form; show raw server text for unknown codes; hard-code a city list; follow a `next` not validated by `safeNext`; show the Profile link to admins; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Agency opens the page | agency session | business profile form prefilled; email section; verification status text; privacy note | N/A |
| Individual opens the page | individual session | existing profile page, unchanged | N/A |
| Admin opens the page | admin session | polite message, no form; no Profile link in the header | N/A |
| Load fails | 5xx, timeout, network | message with Retry, nothing editable | N/A |
| Wrong account type | 403 from the server | polite wrong-account message | N/A |
| Session gone | 401 | signed out, redirect to `/sign-in?next=%2Faccount%2Fprofile` | N/A |
| Guard | Visitor opens the page | redirect to sign-in with `next`; no content | N/A |
| Cities | list loads, empty, fails | select from the server list; empty/failure messages with Retry; stored city preselected | N/A |
| Stored city inactive | city id not in the active list | shown as a disabled "Current city (no longer available)"; other edits still save; city not sent unless changed | N/A |
| Save fields | changed values | PATCH body has only changed fields; success note; header agency name refreshed | N/A |
| Clear description | description emptied | PATCH sends `description: ""`; saved | N/A |
| Nothing changed | no edits | no request; "No changes to save." | N/A |
| Mandatory fields | blank name, phone, address or city | client required errors, no request; server `required` mapped too | N/A |
| Contact details | description with a phone number | server `contains_contact_details` mapped to the explanation under the field | N/A |
| Field errors | other 400 codes | mapped text, summary focus, values kept | N/A |
| Invalid city | `invalid_city` | selection cleared, list re-fetched, error shown | N/A |
| Edit after save | typing | success note clears; typing during an in-flight save is kept | N/A |
| Save failures | 429, 5xx, timeout, network, second 403 | separate generic messages | N/A |
| CSRF self-heal | first 403 | token refetched, retried once | second 403 generic |
| Unknown codes | unknown code, prototype key, unknown field | generic text, never raw server text | N/A |
| Email change | agency requests a change | existing form behaviour; neutral confirmation; pending note | N/A |
| Privacy | after any state | phone and address only inside the form inputs; none in storage, console or URL; none in the header or dashboard | test |
| Links | agency, individual, admin | header and dashboard "Profile" / "Business profile" per type | N/A |
| Accessibility | main states | axe clean at desktop and mobile | test |

</frozen-after-approval>

## Code Map

- `src/app/(app)/account/profile/page.tsx` (type switch), `agency-profile-client.tsx`, `agency-details-form.tsx`; shared extractions under `src/lib/` and `src/components/` (cities loader hook/component shared with `register/agency`)
- `src/lib/*messages*.ts` (agency codes), `src/components/site-header.tsx`, dashboard link
- `tests/e2e/stub-backend.mjs`, `tests/e2e/agency-profile.spec.ts`, README

## Tasks & Acceptance

**Execution:**
- [ ] type switch on the profile route, header and dashboard links
- [ ] business profile client and details form, shared city loader
- [ ] message maps, stub modes, Playwright tests, README

**Acceptance Criteria:**
- Given I am signed in as an Agency, when I open the business profile page, then I can view and edit name, description, city, business phone and office address with validation messages.
- Given the profile fields, when I read the page, then the business phone and office address are shown only to me inside the form and described as private, while name, description and city are described as public.
- Given I enter a phone number or address in the description, when I save, then I see an explanation that descriptions cannot contain them.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Design Notes

Decisions made without asking: one profile route serves both account types so links and the sign-in redirect stay simple; the Agency city select tolerates a stored city that has since been deactivated so an agency can still edit other details; the verification status is shown but never editable because the verification story owns it; the privacy note makes the public/private split visible to the owner; shared pieces are extracted only where they would otherwise be copied verbatim.

## Verification

**Commands (frontend repository):**
- `npm run api:sync && npm run lint && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` -- expected: all pass (the full Playwright run takes about 11 minutes: run it in the background with output to a file)
