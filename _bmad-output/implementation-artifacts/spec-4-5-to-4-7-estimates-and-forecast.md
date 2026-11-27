# Stories 4.5 to 4.7: estimates of published Listings, comparison and 5-Year Price Forecast [BE+FE]

Acceptance criteria: `planning-artifacts/epics.md`, Epic 4 (FR-44, FR-45, FR-46). Behaviour, routes and shapes:
`docs/valuation.md`.

## Scope

- Backend: `valuation` subscribes to `listing.published` and `listing.model_inputs_changed`;
  `selectors.current_estimate`; the owner's recompute of a Published Listing; comparison; forecast stored on
  `PriceEstimate` (migration `valuation.0003`); `GET /api/v1/public/listings/{id}/estimate`.
- Frontend: on the public Listing page, the estimate beside the total Asking price, the verdict, the forecast, the
  single info popover and the owner's "Recompute estimate".

## Decisions

| Question | Decision |
| --- | --- |
| "Publication never waits" | The subscriber only submits to the runtime executor; a full executor drops the work |
| Lazy repair against "a Visitor never computes" | Signed-in readers repair, Visitors read stored rows only |
| Who counts as stale | A row whose input hash differs; a newer model alone does not make it stale |
| Where the estimate is served | Its own public, uncached route, so the Listing response stays cacheable |
| Owner of a hidden Listing | `404` on the public route; the Draft preview shows the estimate |
| Recompute | The existing `POST`, now also for Published; the same 20 an hour |
| Platform computations | Not throttled |
| Rows from before the forecast | `forecast` null, shown as unavailable; a recompute adds it (no backfill) |
| Forecast in the preview | Not shown |
| About equal | Within 5% inclusive of the estimate; the percentage has one decimal |

## Constraints

`docs/story-checklist.md`; only public model inputs reach the model; no estimate changes the Asking price.
