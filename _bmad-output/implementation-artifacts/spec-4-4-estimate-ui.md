# Story 4.4: baseline price in the Listing form, limitation note and About page [BE+FE]

Acceptance criteria: `planning-artifacts/epics.md`, Epic 4, Story 4.4 (FR-43, FR-46, FR-47, SM-4c).

## Scope

- Backend: the Draft preview carries the latest stored estimate (`valuation.selectors.latest_draft_estimate`), and
  the "demo" wording scan over code strings, data files and `openapi.yaml`.
- Frontend: "Get estimate" on the Listing form, the shared info popover, the About AI Price Estimates page, the
  estimate in the preview, the same wording scan.

Not here: estimate on publish and edit (4.5), public Listing page (4.6), forecast (4.7).

## Decisions

| Question | Decision |
| --- | --- |
| Stale estimate in the preview | Shown only while the saved model inputs still match its snapshot hash |
| Stored unavailable state | None: the preview shows nothing when there is no estimate |
| Unsaved edits | "Get estimate" is disabled with a reason until they are saved (the estimate uses the saved Draft) |
| Estimate on screen after an edit | Hidden once a save changes the area or details; title, description and price saves keep it |
| About link in the popover | Opens in a new tab, so unsaved input on the form is not lost |
| Suspended owner | No "Get estimate" section (the route answers `403`) |
| Published, archived or sold Listings | No "Get estimate" action (the route answers `409`); stored estimates come in 4.5 |
| Wording scan | Whole words `demo`, `demos`, `demonstrat…`; docstrings and comments are not user-facing |

## Constraints

`docs/story-checklist.md`; no estimate action changes the Asking price; Publication never depends on an estimate.
