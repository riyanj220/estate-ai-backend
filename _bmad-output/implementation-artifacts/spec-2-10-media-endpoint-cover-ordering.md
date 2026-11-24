---
title: 'Story 2.10: Media endpoint, cover image and ordering (backend half, then frontend half)'
type: 'feature'
created: '2026-11-24'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-9-image-upload-pipeline.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-2-conflict-safe-editing.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-2-conflict-safe-editing-frontend.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-5, AD-15, AD-22, AD-24)'
  - '{project-root}/docs/media.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Draft images can be uploaded and removed (Story 2.9) but cannot be seen, ordered or given a cover, are not part of the Listing resource, and there is no endpoint that shows an image to anyone.

**Approach:** Backend: add a cover flag and a consistent order to `ListingImage`, a revision-checked reorder-and-cover mutation, `media` entries on the Listing resource, and the single Django media endpoint that serves an image only to the Draft's owner (public serving for Published listings arrives later in this epic). Frontend (in `/home/riyan/Riyan/projects/estate-ai-frontend`): an Images section on the Draft page to upload, reorder (with buttons, not drag-only), choose the cover and delete. Backend first and merged, then frontend.

## Boundaries & Constraints

**Always (backend):**
- **Model** (migration `0008_cover`): `ListingImage.is_cover boolean not null default false` with a partial unique constraint (at most one cover per listing: `UNIQUE (listing_id) WHERE is_cover`), `position` stays contiguous 0..n-1 per listing (a service-maintained invariant; the unique `(listing, position)` constraint is made `DEFERRABLE INITIALLY DEFERRED` or updates use a safe two-phase write so reordering never violates it). Existing rows (from Story 2.9) get `is_cover` true for position 0 in the migration (data migration, reversible).
- **Cover rules** (in the service, under the listing row lock): the first image uploaded to a Listing becomes the cover; a later upload never changes the cover; `remove_image` of the cover promotes the image with the lowest remaining position to cover (none left: no cover), and renumbers positions contiguously; removing a non-cover keeps the cover and renumbers.
- **Mutation `listings.services.set_image_order(actor, listing_id, base_revision, order, cover_id)`**: `order` is the full list of the Listing's image ids in the wanted order (must be exactly the current set, no duplicates, no unknown ids: otherwise `validation_error` with field code `invalid` on `order`), `cover_id` optional (`null`/omitted keeps the current cover; a given id must be one of the Listing's images, else `invalid` on `cover_id`; a Listing with images always keeps exactly one cover). Same access rules as the other image services (Visitor `unauthenticated`; non-owner `not_found`; `edit_drafts` capability; editable status), revision-checked exactly like `apply_updates` (`base_revision` positive integer, stale -> `StaleRevision` with the current `ListingView`, nothing written); a request that changes nothing returns the current view with no revision bump and no revision row; otherwise one `ListingRevision` (`changes` `{"images": n, "order": true|false, "cover": true|false}`; never ids) and `revision` +1. Upload and remove keep bumping the revision as in 2.9.
- **Wire (AD-24)**: the Listing resource's `media` is now a list ordered by `position` of `{id, position, is_cover, content_type, width, height, bytes, url}` where `url` is the relative path of the media endpoint (`/api/v1/listings/{id}/images/{image_id}/file`); `owner_account_id`, the asset id and the storage key never appear; `media` is `[]` for a Draft with none. The ImageView returned by upload and the update responses use the same item shape.
- **API**: `PATCH /api/v1/listings/{id}/images` with a strict body `{base_revision, order, cover_id?}` (signed in, CSRF, `no-store`, other keys `unknown_field`); `200` returns the Listing resource; `409` `stale_revision` with the `current` Listing exactly like the field PATCH (reuse the `ListingConflict` schema); `400` field-level codes; `404` for non-owners; `403` capability. `GET /api/v1/listings/{id}/images/{image_id}/file` is THE media endpoint: it applies the access rules at request time (AD-15, AD-22): for a Draft only to the owning, non-administrator account actor with `view_drafts` (Suspended and Email-unverified owners may view; Deactivated cannot), `404` identical for non-owners, administrators, Visitors (a Visitor gets 404, not 401, so existence is never confirmed), unknown or malformed ids and ids of another listing; for a Listing whose status is not `draft` it returns `404` in this story (public serving and the central visibility policy come in Stories 2.12 and 2.15; leave one clearly named seam, `listings.selectors.image_for_serving(actor, listing_id, image_id)`, that Story 2.12 will rewire to the single policy). The response streams the stored (re-encoded) bytes with the stored `Content-Type`, `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`, `Content-Disposition: inline` without a file name, `Content-Length`, a restrictive `Content-Security-Policy: default-src 'none'; sandbox`, no cookies set, and supports `HEAD`; no `Range`, no `ETag` (documented). The request never reveals the storage key. OpenAPI documents the routes and the binary response.
- **Retention/storage**: reorder never touches files; `remove_image` still deletes the asset and file after commit.
- **Docs and tests**: `docs/media.md` (serving rules, headers, cover/order rules), `docs/listings.md` (media wire, mutation, revision rules), `docs/authorisation-matrix.md` and `openapi.yaml` regenerated; tests for every matrix row below including threaded concurrency (two reorders with the same base: exactly one wins), the migration data step, the partial unique constraint, constraint drift, the media endpoint access matrix (owner, other owner, admin, Visitor, Suspended owner, Deactivated owner, unverified owner, non-draft listing, wrong listing, malformed ids), response headers, a byte-for-byte check that the served bytes equal the stored bytes and contain no metadata, and canaries that no key/path/owner id appears in responses or logs. Lessons from earlier stories apply (whole-body echo checks minus the request id, after-commit/rollback tests that can really fail, no vacuous assertions, static neutral messages).

**Always (frontend, after the backend is merged and `npm run api:sync`):**
- An "Images" section on the Draft page (`/account/listings/[id]`), between the summary and the editor form (its own component file; it does NOT share the field form's Save): shows each image as a thumbnail loaded from its relative `url` (same-origin, through the existing proxy; verify the proxy forwards binary responses and large multipart request bodies up to 6 MiB, and fix/document its body-size limit if needed), with `alt` text "Image N of M" plus "Cover image" for the cover; an upload control (`<input type="file" multiple accept="image/jpeg,image/png,image/webp">` with a visible label and a button), uploading files ONE AT A TIME (sequential) through `withCsrf` as multipart `FormData` with a single `file` part, with per-file status (waiting, uploading, done, failed with the mapped message), a hard stop at 10 images (client hint only; the server decides), and focus management; for each image: "Move earlier", "Move later" (buttons, disabled at the ends via `aria-disabled`), "Set as cover" (not shown/disabled for the cover), "Delete" (confirmation step, then `DELETE`). Reorder and cover send `PATCH .../images` with the current `revision` as `base_revision` and the full order; a `409` replaces the shown list with `current.media`, keeps focus sensibly, and shows a conflict notice ("Your images were changed in another session. We loaded the latest order."). The Draft page's revision shown in the header and the Details form's `base_revision` must stay in sync: every image mutation response (the Listing resource) updates the page's listing state (revision and media) so a following field save does not conflict; a field-editor conflict reload also refreshes `media`. Error mapping by code for upload: `too_large` ("Images must be 5 MB or smaller"), `unsupported_type`, `too_large_dimensions`, `too_small`, `invalid_image`, `storage_cap_exceeded` ("You have used all of your image storage. Delete an image to upload more."), `too_many_images` ("A listing can have at most 10 images."), `busy` (try again shortly, Retry-After respected as text only), 429, 401 (signed out), 403 (suspended only if the session says so, else generic), 404 (the Draft not-found state), network and timeout; known codes only, never server text. An empty state ("No images yet. A listing can be saved as a Draft without images, but needs at least one to be published."). Thumbnails never persist anywhere (no storage, no object URLs kept after unmount; revoke any created preview URL); no console output of file names; privacy test. Counts and states announced in one polite live region (no per-keystroke announcements). Accessibility: all controls keyboard-operable, visible focus, axe clean at desktop and mobile in the empty, populated, uploading, error and conflict states; reduced-motion respected.
- Stub backend: stateful images per Draft (upload via multipart with real rules mirrored: type by magic bytes for PNG/JPEG/WebP, 5 MB cap, 10 limit, a simulated account cap mode, `busy`, rate limit, 401/403/404 modes, `lost response`), PATCH images with real revision/409 rules, DELETE with cover promotion and contiguous positions, `GET .../file` serving a tiny valid PNG, `media` in the listing resource. Tests per matrix row (use `setInputFiles` with small in-memory PNGs generated in the test), README, no serial mode, wait for stub counters before negative assertions, aria-disabled not disabled, low-entropy values.

**Never:** serve any image to anyone but the Draft's owner in this story; expose a storage key, asset id or owner id; set caching that lets a shared cache keep a Draft image; trust a stored content type for anything but the response header; drag-only reordering; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First upload | Draft with no images | the image is the cover, position 0 | N/A |
| Later uploads | Draft with images | appended at the end; cover unchanged | N/A |
| Reorder | `order` = all ids permuted, fresh `base_revision` | positions rewritten 0..n-1, revision +1, one revision row | N/A |
| Choose cover | `cover_id` = another image | exactly one cover afterwards | N/A |
| No change | same order and cover | 200 current, no revision bump, no row | N/A |
| Bad order | missing id, duplicate, unknown, extra, non-list, foreign listing's id | `invalid` on `order`; nothing written | envelope |
| Bad cover | id not in this Listing | `invalid` on `cover_id` | envelope |
| Stale revision / replay | old `base_revision` | 409 `stale_revision` with `current`; nothing written | envelope |
| Concurrent reorders | two with the same base | exactly one 200, one 409 | test |
| Delete the cover | cover removed | next lowest position becomes cover, positions contiguous | N/A |
| Delete the last image | only image removed | `media` empty, no cover | N/A |
| Draft with no images | Draft | `media: []`; saving other fields unaffected | N/A |
| Serve to the owner | owner GETs an image of their Draft | 200 bytes, stored content type, `private, no-store`, `nosniff`, CSP sandbox, no file name | N/A |
| Serve to others | other owner, administrator, Visitor, unknown/malformed ids, another listing's image id, deactivated owner | identical 404 | envelope |
| Suspended / unverified owner | owner in these states | may view their own images (200) | N/A |
| Non-draft listing | status other than draft | 404 (public serving later) | envelope |
| HEAD / Range | HEAD; Range header | HEAD works without a body; Range ignored (200 full) | test |
| Access rules for edits | non-owner / admin / Visitor / suspended | 404 / 404 / 401 / 403 as the other Draft edits | envelope |
| Wire | listing resource | `media` items without ids of assets/keys/owners; `url` relative | test |
| Privacy | logs, errors, responses | no storage key, path, owner id | canary test |
| Contract | `openapi.yaml`, authorisation matrix | routes, schemas, binary response documented; fresh | test |
| Frontend: populated | Draft with images | thumbnails in order, cover marked, buttons per image | N/A |
| Frontend: upload | several files | sequential, per-file status, failures mapped, limit message at 10 | N/A |
| Frontend: reorder/cover/delete | click actions | PATCH with revision, list updates, focus kept; delete asks first | N/A |
| Frontend: conflict | 409 | list replaced by `current.media`, notice shown, nothing lost silently | N/A |
| Frontend: revision sync | image change then a field save | no spurious 409 on the field save | N/A |
| Frontend: empty / errors | no images; each error code | empty-state text; mapped messages | N/A |
| Frontend: privacy and a11y | any state | no storage/console leak; axe clean desktop and mobile; keyboard-only works | test |

</frozen-after-approval>

## Code Map

- Backend: `apps/listings/{models,services,selectors}.py`, `migrations/0008_cover.py`, `apps/presentation/api/{listing_images,listings}.py`, `config/urls.py`, `apps/kernel/storage.py` (read only), docs, `openapi.yaml`, tests
- Frontend: `src/app/(app)/account/listings/[id]/{draft-client,images-section}.tsx` (new), `src/lib/{listing-labels,listing-messages}`, proxy config, `tests/e2e/{stub-backend.mjs,listing-images.spec.ts}`, README

## Tasks & Acceptance

**Execution:**
- [ ] Backend: cover flag and order maintenance, `set_image_order`, `media` in the wire, the media endpoint, docs, tests
- [ ] Frontend: Images section (upload, reorder, cover, delete), conflict and revision sync, stub, tests, README

**Acceptance Criteria:**
- Given several images, when I choose a cover and reorder then the Listing has at most one cover and a consistent order; deleting the cover promotes the next image, and a Listing with no image has no cover (Publication blocking is Story 2.15).
- Given a Draft image, when anyone requests it then it is served only by the Django media endpoint, which applies the visibility rule, and for a Draft only to its owner; a Draft may have no images.
- Given the frontend, when I manage images then I can upload, reorder, set the cover and delete, and the accessibility check passes.

## Implementation Notes

Decisions beyond the story text: reorder and cover are one revision-checked mutation (`PATCH .../images`); upload and delete bump the revision but need no `base_revision`; non-draft listings are not served yet (a named seam for Story 2.12); a Visitor gets 404 (not 401) on the media endpoint.

## Spec Change Log

## Review Triage Log

## Design Notes

One mutation with the full order keeps positions contiguous without per-pair swaps and races. Serving is deliberately the narrowest possible (owner, Draft) behind one seam so the later single visibility policy replaces it in one place.

## Verification

**Backend:** `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check .; uv run ruff format --check .; env -u DJANGO_SETTINGS_MODULE uv run pytest -q; uv run python manage.py makemigrations --check --dry-run; uv run python manage.py spectacular --validate --file openapi.yaml` (openapi regenerated deliberately).
**Frontend:** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && PW_CHANNEL=chrome npx playwright test` (full run in the background; never two at once).
