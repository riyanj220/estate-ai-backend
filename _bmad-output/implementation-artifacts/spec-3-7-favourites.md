# Story 3.7: Favourites [BE+FE]

Acceptance criteria: `planning-artifacts/epics.md`, Story 3.7 (FR-40). Checklist: `docs/story-checklist.md`.

## Backend

- `listings.SavedListing` (plain `account_id`, FK to the Listing, unique per pair). Migration `0018_saved_listing`.
- `services.save_favourite` / `remove_favourite`, `selectors.list_favourites` / `favourite_ids`, all actor first.
- `GET /api/v1/me/favourites`, `GET .../ids`, `PUT` and `DELETE .../{listing_id}`; `no-store`, CSRF. Behaviour: `docs/listings.md`.

## Decisions

| Question | Decision |
| --- | --- |
| Who | Individual and Agency. Active and Email-unverified save, view, remove; Suspended view and remove only; Deactivated and administrators `forbidden` |
| Capabilities | No new capability: `view_drafts` and `edit_drafts` cover exactly those account states |
| Save again | `204`, even if the Listing is no longer public (nothing is disclosed) |
| Not saved on remove | `not_found` (also another account's favourite) |
| Cap | Existing `favourites` policy (500); `429 throttled` without `Retry-After`; account row lock serialises saves |
| Unavailable item | `listing_id`, `saved_at`, `listing: null` |
| Listing deletion | Favourites cascade (only Drafts and sample data are ever deleted) |
| Account deactivation | Removing favourites belongs to the deactivation story |
| Saved state on public pages | Separate `ids` read, so public answers stay the same for every caller and cacheable |
| Privacy gates | The list is classified `OTHER_ACCOUNT` (it shows other accounts' Listings) |

## Frontend

- Save / Saved button on public cards and detail; a Visitor gets a sign-in or create-account prompt.
- `/account/favourites` (header and dashboard link): cards with Remove, "No longer available" rows, Load more; cap message.
- Browse card moved to `src/components/listing-card.tsx` and shared. Stub routes and `tests/e2e/favourites.spec.ts` (axe).
