# Story 4.3: request an AI Price Estimate and supportability [BE]

Acceptance criteria: `planning-artifacts/epics.md`, Epic 4, Story 4.3 (FR-42, FR-46, AD-12). Behaviour, routes and
shapes: `docs/valuation.md`.

## Scope

- `POST /api/v1/estimates` and `POST /api/v1/listings/{id}/estimate`.
- `valuation.supportability`, `PriceEstimate` (migration `valuation.0002`), inference on the bounded executor,
  AI request log, `estimates` throttle (the policy was already seeded by `kernel.0002`; no new seed).
- New event `listing.deleted`, `listings.selectors.existing_listing_ids`, retention job `valuation.estimates`.

Not here: estimate on publish and edit, `current_estimate`, preview, forecast (4.4 to 4.7).

## Decisions

| Question | Decision |
| --- | --- |
| Unsupported or failed | `200`, `status: unavailable`, reason code and static message, no number |
| Executor full or timeout | The same shape (`temporarily_unavailable`), not `503` |
| Own non-Draft Listing | `409 conflict` (published Listings get stored estimates in 4.5) |
| Who may request | Accounts that may edit Drafts (not Suspended, Deactivated or administrators) |
| Entered attributes | Judged by the Listing field rules; any non-input field is refused |
| Draft deletion | `listing.deleted` removes at once; the retention job repairs a miss or a delete racing a request |
| Rounding | Nearest PKR 1 000 |

## Constraints

`docs/story-checklist.md`; `valuation` imports no other app's models; no private value is stored, logged or returned.

## Open

Account erasure (a later epic) must also remove `PriceEstimate` rows by `account_id`.
