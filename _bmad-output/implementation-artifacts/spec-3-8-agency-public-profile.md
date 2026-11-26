# Story 3.8: Agency public profile and Agency Listings [BE+FE]

Acceptance criteria: `planning-artifacts/epics.md`, Story 3.8 (FR-41). Checklist: `docs/story-checklist.md`.

## Backend

- `GET /api/v1/public/agencies/{agency_id}`: `{name, logo_url, description, verified}`.
- `GET /api/v1/public/agencies/{agency_id}/listings`: the public list narrowed to the Agency.
- `identity.selectors.public_agency` / `public_agency_owner_id`, sharing one gate with the logo route.
- `search_public(..., owner_account_id=)`; one view helper serves both lists. No migration.
- `advertiser.agency_id` added so a Listing can link to its Agency. Behaviour: `docs/visibility.md`.

## Decisions

| Question | Decision |
| --- | --- |
| Id | The public Agency profile id (as the logo route), never the account id |
| "Active" | Active account only: Email-unverified is `404` like Suspended and Deactivated (as the logo) |
| Order of checks | The Agency `404` comes before parameter validation |
| Cursors | Unchanged keyset cursor; it holds a position only, so it is not bound to the Agency |
| Caching | As the other public reads: `public, max-age=60`, errors `no-store` |
| City | Not shown: the criteria list name, logo, description and Verification only |

## Frontend

- Server-rendered `/agencies/{id}`: profile, filter panel and listing cards; not-found state for `404`.
- The advertiser name on cards and detail links to it. Stub routes and `tests/e2e/agency-page.spec.ts` (axe).
