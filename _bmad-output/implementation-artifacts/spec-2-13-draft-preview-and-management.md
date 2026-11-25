---
title: 'Story 2.13: Draft preview and management (backend half, then frontend half)'
type: 'feature'
created: '2026-11-25'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-12-visibility-policy-and-public-projections.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-10-media-endpoint-cover-ordering.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-8-amenities-and-nearby-facilities.md'
  - '{project-root}/_bmad-output/planning-artifacts/epics.md (Story 2.13)'
  - '{project-root}/docs/visibility.md'
  - '{project-root}/docs/media.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An owner can create and edit Drafts, but cannot see what the public will see, cannot throw a Draft away, and abandoned Drafts live forever.

**Approach:** Backend: an owner-only preview built from the public projection type, an owner delete of an unpublished Draft (rows and images), and two retention jobs on the existing `run_retention` framework (`apps/kernel/retention.py`, register from `AppConfig.ready`): a notice after 180 days without activity and deletion 30 days later unless opened or edited. Frontend: a Preview page, a Delete action with a confirmation, and a readable in-app notification. Backend first and merged, then frontend. "Save, reopen and list incomplete Drafts" already works (Stories 2.1 to 2.8); this story adds a test that pins it (an incomplete Draft with only a purpose and a title is listed, reopened and edited) and nothing else for it.

## Boundaries & Constraints

**Always (backend):**
- **Preview**: `GET /api/v1/listings/{id}/preview`, owner only (same 404 for a non-owner, an administrator and a missing id; 401 for a Visitor), allowed for the owner's own `draft` and `published` Listing, `Cache-Control: no-store`. The body is the frozen `PublicListingView` shape (the 2.12 type and its explicit serializer), built from a selector `listings.selectors.preview_of(actor, listing_id)` that reuses the same projection function the public projection uses (no second field list, no copy of `public_fields`) but skips the `is_public` gate because the viewer is the owner. It must never contain a private-class field (street, house number, latitude, longitude), a phone number, the revision number, the provenance, status internals or any other owner-only data. It carries the public images in the same shape as the public projection (image URLs are the public image routes; a Draft image that is not public yet is served by the owner-only route, so the preview uses the owner image URL for images that are not public) and `estimate: null` (a key that is always null until the valuation feature exists). Preview counts as "opened" (see retention). Allowed for Active and Email-unverified owners (`view_drafts` capability); a Suspended owner may view too; Deactivated is denied like the rest.
- **Delete**: `DELETE /api/v1/listings/{id}` → `204`, CSRF checked like the other writes, owner only (non-owner, admin, unknown id: the same 404; Visitor 401). Only a `draft` can be deleted this way; a Published (or any other non-draft) Listing answers `409 conflict` with the existing static message style and nothing changes. Capability `edit_drafts` (Suspended and Deactivated denied). Service `listings.services.delete_draft(actor, listing_id)` actor-first, one transaction: lock the Listing row, check owner and status, delete every image through `media.delete_asset(actor, asset_id, missing_ok=True)` (usage given back, files deleted after commit; a failing file delete never fails the request; the existing orphan purge covers files within 24 hours), delete the Listing and every row that belongs to it (location, private address, amenities, details, images links, revisions) so nothing of the Draft remains, write an audit row `listing.draft_deleted` (listing id and account only, never any content or private value), and nothing else. Repeating the DELETE is `404`. No effect on the open-draft count except freeing a slot (the cap counts rows). A concurrent edit or image upload racing the delete ends in a clean `404`/`409`, never a 500 or an orphan (lock order account then listing as elsewhere; test with two threads).
- **Retention (extends `run_retention`, no new command)**: record `last_opened_at` on the Listing (nullable timestamp). It is set when the owner opens the Draft (`GET /api/v1/listings/{id}`) or previews it, but at most once per hour per Listing (an update only when the stored value is missing or older than one hour) and never bumps `revision`, `updated_at` of content, or writes a revision row. Activity time = max(`updated_at`, `last_opened_at`, `created_at`). Two registered jobs from `listings` `AppConfig.ready`: `listings.draft_notice(now, dry_run)` selects `draft` Listings whose activity time is older than 180 days, whose owner is not Suspended, and that have no notice yet; sets `expiry_notice_sent_at = now` and `delete_after = now + 30 days` in the same transaction and raises the notification after commit; returns how many it noticed. `listings.draft_expiry(now, dry_run)` selects `draft` Listings with `delete_after <= now` and activity time not later than `expiry_notice_sent_at` (opened or edited since the notice cancels: when activity is later than the notice, clear both columns so a later inactivity restarts the 180 days), and deletes them through the same delete path (media, rows, audit `listing.draft_deleted` with `reason: retention`), returns how many it deleted. Dry run changes and notifies nothing and returns the would-be counts. Idempotent: a second run the same day does nothing. Published and other non-draft Listings are never touched. Per-listing failures are isolated (one bad Listing does not stop the others; log the class only). Notification kind `listing.draft_expiring`, registered in `AppConfig.ready` with a strict schema (the listing id as a short text and the number of days left as an integer; never the title, address or any content), raised through `kernel.notifications.notify` after commit, to the owner only. The notification payload leaks nothing and the frontend renders a fixed sentence from the kind.
- **Migration** `listings 0012`: `last_opened_at`, `expiry_notice_sent_at`, `delete_after` (all nullable) and an index to select retention candidates cheaply (draft status plus activity columns). Written only through the owning services.
- **Docs/tests**: `docs/listings.md`, `docs/visibility.md` (preview), `docs/media.md` (delete and files), `docs/authorisation-matrix.md`, `docs/reference-data.md` only if touched; a retention section somewhere findable (`docs/retention.md` if one exists, else `docs/listings.md`). Tests for every matrix row: preview shape equals the public projection shape for the same Listing (assert the key sets match, assert no private key and no phone), canary private values never in the preview, estimate null; delete (owner, other account, admin, Visitor, published, missing, repeat, Suspended, images removed with usage given back, rows gone including revisions, audit has no content, race with a concurrent save/upload); retention (180 days exactly at the boundary, 179 days untouched, notice once, owner Suspended skipped, opened and edited cancels, published untouched, delete after 30 days, dry run, idempotent second run, per-listing failure isolation, time frozen with explicit `now`, mutation-checked); incomplete Draft is listed, reopened and edited. Lessons from earlier stories apply (static messages, whole-body checks, rollback tests that can fail, low-entropy fake words, after-commit effects last).

**Always (frontend, after the backend is merged and `npm run api:sync`):**
- A **Preview** page `/account/listings/[id]/preview` (server-guarded like the Draft page; `no-store`): renders the public projection exactly as received, a clear "This is a preview. Only you can see it." banner, an empty "Estimate" area that reads "Not available yet", no private address, no phone, no edit controls; a link back to the Draft; load, not found and failed states; axe clean at desktop and mobile. Draft page gets a "Preview" link; My listings rows get a Preview link too.
- A **Delete draft** action on the Draft page (only for a `draft`), a real accessible confirmation (modal dialog with focus trap and return focus, Escape cancels, names what will be deleted: "This deletes the draft and its images. This cannot be undone."), `DELETE` with CSRF, on success navigate to My listings with a polite confirmation, on `404` treat as already gone, on `409` explain that a published listing cannot be deleted here, failures show an error and keep the page. Unsaved edits are discarded without a second prompt. The image lock/save lock from earlier stories must prevent deleting while an image upload or a save is in flight.
- The in-app notification list renders `listing.draft_expiring` as a fixed sentence ("Your draft has had no activity for a long time and will be deleted in N days unless you open it.") linking to the draft; unknown kinds keep their existing generic rendering. Stub backend updated (preview route, delete route with the 409/404 rules, the notification kind), README, privacy test (no private value or phone anywhere on the preview page, no storage, nothing logged), production-build check extended for `no-store` on the preview and the delete route through the proxy.

**Never:** show a private-class field or a phone number in the preview; delete a non-draft Listing; leave images, usage or revision rows behind; include a title or any content in a notification, audit row or log; bump `revision` when opening; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Preview own Draft/Published | owner GET preview | public-projection shape, `estimate: null`, no private fields | N/A |
| Preview by another account / admin / missing | any | `404`, nothing leaked | envelope |
| Preview by Visitor | no session | `401` | envelope |
| Delete own Draft | owner DELETE | `204`; rows, images, revisions gone; audit without content; usage given back | N/A |
| Delete Published | owner DELETE | `409`, nothing changed | envelope |
| Delete twice / other account / admin | any | `404` | envelope |
| Delete while a save/upload races | two writers | one wins, the other `404`/`409`; no orphan, no 500 | test |
| Open Draft | owner GET | `last_opened_at` set at most hourly; revision unchanged | N/A |
| 179 / 180 days idle | job run | untouched / notice sent once, `delete_after` = +30 days | N/A |
| Opened or edited after the notice | job run | notice cleared, not deleted | N/A |
| Past `delete_after`, still idle | job run | deleted like an owner delete (reason retention) | N/A |
| Suspended owner | job run | skipped | N/A |
| Dry run / second run | job run | nothing changes / nothing more happens | N/A |

</frozen-after-approval>

## Spec Change Log

## Review Triage Log

### Backend review (decisions, including departures from the spec text)

- **Lost notice:** `expire_idle_draft` deletes only when a stored `listing.draft_expiring` notification for the Listing
  exists (`kernel.notifications.has_with_payload`); otherwise it re-issues the notice, sets `delete_after = now + 30 days`
  and deletes nothing that run. Departure: the spec deleted purely on dates.
- **Re-grace:** a due Draft whose `delete_after` is more than 2 days past (owner Suspended meanwhile, or the job did not
  run) is not deleted: `delete_after = now + 7 days`, notice re-issued with `days_left: 7`, counted as neither deleted
  nor noticed. Departure from the spec's flat "30 days after the notice".
- **Poison pills:** candidates are read in keyset pages by id (200 per page, at most 50 pages per run) across the run.
- **Deactivated owners** are skipped like Suspended ones, in the selectors and the locked re-check. Departure: the spec
  named Suspended only.
- **Best-effort open:** `GET` detail and preview log and swallow a failing `record_open` (class only).
- Removed the dead `expiry_notice_sent_at is None` branch (the paired check constraint makes it impossible); corrected
  the `record_open` comment.
- Added tests: paired-columns constraint, migration 0012 on existing rows, deterministic lock tests for delete and expiry,
  exact preview values, serializer/projection drift, direct re-check cases, `record_open` for unverified and Suspended
  owners, Deactivated retention, UUID_TEXT strict forms.
- Delete route documents 401/403/404/409; docs updated (hourly open write, skipped states, re-grace rules, notification
  kinds not enumerated in the contract).
- **Deferred / noted:** the idle partial index does not serve the `GREATEST(...)` expression (fine at MVP scale);
  `GREATEST` NULL handling is Postgres-only.

## Verification

**Backend:** `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check .; uv run ruff format --check .; env -u DJANGO_SETTINGS_MODULE uv run pytest -q; uv run python manage.py makemigrations --check --dry-run; uv run python manage.py spectacular --validate --file openapi.yaml` (openapi regenerated deliberately).
**Frontend (new workflow, see the frontend README):** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && npm run check:prod-admin && npm run check:prod-listings && npm run check:prod-profile`, the new and changed specs, and `npm run test:e2e:changed`; the full suite runs in CI, not locally.
