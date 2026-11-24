---
title: 'Story 2.9: Image upload pipeline and storage (backend)'
type: 'feature'
created: '2026-11-24'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-2-conflict-safe-editing.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-14 runtime, AD-15 media, AD-18, AD-22, AD-23)'
  - '{project-root}/docs/runtime-and-retention.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Listings cannot show a property because there is no safe way to accept, process and store images, no per-account storage limit, and no cleanup of uploads that never become part of a Listing.

**Approach:** Add a generic, reusable media pipeline in `kernel` (shared later by Story 2.11 for profile pictures and logos): a storage port with a filesystem adapter, an image-processing function that validates and re-encodes on the existing bounded executor, a `MediaAsset` record with an atomic per-account storage cap, and a retention hook that removes assets never attached within 24 hours. In `listings`, add `ListingImage` (an attached asset) and the service and endpoint that let a Draft's owner upload up to 10 images. Serving images is Story 2.10; this story never serves a file. Backend only.

## Boundaries & Constraints

**Always:**
- **Storage port** (`kernel/storage.py`): a small interface (`put(key, data) -> None`, `get(key) -> bytes` for the media endpoint to come, `delete(key)`, `exists(key)`) with a filesystem adapter rooted at a configured `MEDIA_ROOT` outside any web-served directory (settings; tests use a temporary directory; dev default under the project but git-ignored). Keys are random (`secrets.token_urlsafe(32)`-style, not derived from names, owners or listings) and sharded into two-level directories; the adapter refuses keys that are not the exact random format (no path traversal). No public URL is ever produced by the port. Writes are atomic (temp file + rename).
- **Image pipeline** (`kernel/images.py`, Pillow added as a dependency, imported ONLY in `kernel`; add an import-linter/architecture rule that no other app imports `PIL`): `process_image(data: bytes) -> ProcessedImage(data, content_type, width, height)`. Order: (1) size cap checked on the byte length BEFORE decoding (reject over 5 MB: `too_large`); (2) the type is decided by the decoded format, never by the filename or declared content type, and must be JPEG, PNG or WebP (`unsupported_type`; GIF/SVG/TIFF/HEIC/BMP/animated formats are refused, an animated WebP or APNG is refused); (3) decompression-bomb protection: `Image.MAX_IMAGE_PIXELS` set explicitly and the pixel count (width x height) checked from the header before the full decode, maximum 40 megapixels and each side at most 10,000 (`too_large_dimensions`), minimum 200 x 200 (`too_small`), and Pillow's bomb warnings treated as errors; (4) corrupt, truncated or polyglot data (`invalid_image`); (5) the image is fully decoded, EXIF orientation applied, converted to RGB (PNG with alpha keeps alpha as PNG; JPEG/WebP RGB), and RE-ENCODED to a fresh file (JPEG quality 85 progressive, PNG optimised, WebP quality 85) so ALL metadata (EXIF, XMP, ICC beyond sRGB conversion, GPS, comments, trailing bytes) is dropped; the output is never the input bytes. The long side is downscaled to at most 2,048 px (never upscaled). The work runs on the existing `kernel.runtime` bounded executor with a timeout (20 s) and never blocks the event loop thread beyond that executor; a timeout or executor-full condition is a retryable `busy` error. Errors are domain errors with codes and static messages in the catalogue (no echo of file names, no decoder error text).
- **Media assets and caps** (`kernel/models.py`, `kernel/media.py`): model `MediaAsset(id uuid, owner_account_id uuid (plain column), storage_key unique, content_type, bytes int, width, height, created_at, attached_at null)` and `StorageUsage(account_id pk, bytes_used bigint)`; service `store_image(actor, data) -> MediaAsset` (actor-first, AD-22: only an authenticated, non-administrator account actor): processes the image, then in ONE transaction locks the account's `StorageUsage` row (`select_for_update`, created on first use), refuses with `storage_cap_exceeded` if `bytes_used + new bytes` exceeds the account's cap, otherwise increments `bytes_used`, writes the asset row, and the file is written to the store BEFORE the transaction commits and removed again if the transaction fails (no file without a row except crash windows, which retention cleans). Caps come from a seeded policy `storage_cap_bytes` read by account type (Individual 200 MB = 209,715,200; Agency 1 GB = 1,073,741,824) through the existing cap-policy mechanism used for `open_drafts` (administrator editing of the value is Story 2.20); the cap counts the processed (stored) size. `delete_asset(actor, asset)` removes the row, decrements usage (never below zero) in one transaction and deletes the file after commit. Concurrency: parallel uploads from one account can never exceed the cap (threaded test).
- **Orphan retention**: a registered retention hook (the mechanism `run_retention` already provides) deletes `MediaAsset` rows with `attached_at is null` and `created_at` older than 24 hours, decrements usage, and removes their files; it also removes stored files that have no row older than 24 hours (listing the store; bounded batches; never touches files younger than 24 h); every deletion is idempotent and a failure on one file never stops the rest. Documented in `docs/runtime-and-retention.md`.
- **ListingImage** (`listings/models.py`, migration `0007_images`): `ListingImage(id uuid, listing FK, asset_id uuid (plain column, unique), position int, created_at)`; the asset's `attached_at` is set in the same transaction as the row. Maximum 10 per Listing (count and insert under the listing row lock, so races cannot exceed it): `too_many_images`. Position is the next integer (the reorder/cover feature is Story 2.10; the cover is the lowest position until then). Deleting a Draft image (service `remove_image`, owner only, Draft only) deletes the row and the asset. Removing a Listing's images when the Listing is deleted is Story 2.17 (document).
- **Service `listings.upload_image(actor, listing_id, data)`**: Visitor `unauthenticated`; the listing must be owned by the actor's account else `not_found` (identical for everyone else, no existence leak); capability `edit_drafts` required (forbidden for Suspended etc., evaluated after the not-found rule); the listing must be in an editable status; then `kernel.media.store_image` and attach, bumping the Listing `revision` by one with a `ListingRevision` row whose `changes` records `{"images": count}` (no file data), so a concurrent editor sees the change; returns an `ImageView(id, position, content_type, width, height, bytes)`. Stale detection is NOT required for uploads (no `base_revision`).
- **API**: `POST /api/v1/listings/{id}/images` (signed in, CSRF enforced, `no-store`, multipart form with one `file` part; reject any other part and more than one file with a validation error; the request body size is capped at 6 MB at the view before reading into memory, with Django's `DATA_UPLOAD_MAX_MEMORY_SIZE`/`FILE_UPLOAD_MAX_MEMORY_SIZE` settings aligned); `201` returns the `ImageView` JSON (never a URL, never the storage key); `DELETE /api/v1/listings/{id}/images/{image_id}` (`204`); `400` carries code `validation_error` with a field code keyed `file` (`too_large`, `unsupported_type`, `too_large_dimensions`, `too_small`, `invalid_image`); an oversize body maps to the same `validation_error`/`too_large`; `429` for the upload rate limit (seeded rate limit `image_upload`, 30 per hour per account, using the existing limiter); `404` for non-owners; `403` for the capability refusal; `409` with the codes `storage_cap_exceeded` and `too_many_images` in the standard error envelope with static messages. OpenAPI documents the routes, schemas and codes; the authorisation matrix is regenerated.
- **Security**: the `Content-Type` header and file name are never trusted or stored; no file name is kept anywhere; stored files are never executed or directly served; logs never contain file data, names or keys; image bytes never enter an error body; EXIF (including GPS) is proven stripped by a test that uploads an image carrying EXIF GPS and a comment and asserts the stored bytes contain neither (byte search plus Pillow re-read).
- **Docs and tests**: new `docs/media.md` (port, pipeline limits and error codes, caps, retention, security properties), updates to `docs/listings.md`, `docs/runtime-and-retention.md`, README (Pillow system needs if any) and `openapi.yaml`; tests for every matrix row using small generated images (Pillow-made in-test; no binary fixtures larger than 50 KB checked in); the test settings use a temp `MEDIA_ROOT`. All earlier tests, convention tests and import-linter contracts keep passing (plus the new `PIL` rule).

**Never:** trust the upload's declared type or name; store or serve the original bytes; derive a key from user input; echo decoder errors; exceed a cap under concurrency; leave a file referenced by no row after a failed transaction in the normal path; import Pillow outside `kernel`; serve a file from this story's code; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid JPEG/PNG/WebP | owner uploads a 1200x800 image under 5 MB | `201` ImageView, position next, asset stored under a random key, re-encoded, revision +1 | N/A |
| Re-encode and strip | image with EXIF GPS, comment, ICC, trailing bytes | stored bytes contain none of them; orientation applied; long side at most 2048 | test |
| Type by content | PNG renamed `.jpg`; header says `image/jpeg`; SVG, GIF, TIFF, BMP, HEIC, PDF, HTML, animated WebP/APNG | decided by decoded format: PNG accepted, the rest `unsupported_type` | envelope |
| Size cap | 5 MB + 1 byte; 6 MB body | `too_large` before decode; nothing stored | envelope |
| Pixel limits | 41 MP image, side over 10,000, decompression bomb (tiny file huge dims), under 200 px | `too_large_dimensions` / `too_small`; no memory blow-up | envelope |
| Corrupt / truncated / polyglot | garbage, truncated JPEG, JPEG with appended ZIP | `invalid_image` (polyglot re-encoded clean or refused) | envelope |
| Per-listing limit | 11th image | `409` `too_many_images`; nothing stored; race of two uploads at 9 images admits exactly one | envelope |
| Storage cap | Individual at cap; Agency 1 GB; threaded uploads | `409` `storage_cap_exceeded`; `bytes_used` never exceeds the cap | envelope |
| Atomic cap | N parallel uploads each fitting alone but not together | exactly the number that fit are stored | test |
| Failure after file write | DB failure after the file write | file removed, usage unchanged, no asset row | test |
| Non-owner / admin / Visitor / unknown id | any | 404 (Visitor 401); never 403 for ownership; no existence leak | envelope |
| Suspended / deactivated owner | `edit_drafts` denied | 403 `forbidden`; nothing stored | envelope |
| No CSRF / wrong method / extra parts | PATCH or two files or a non-file `file` field | 403 / 405 / validation error | envelope |
| Remove image | owner deletes a Draft image | row and asset removed, usage decremented once, file deleted after commit; repeat is 404 | N/A |
| Orphans | asset never attached, older than 24 h; stray file with no row older than 24 h; younger ones | removed by `run_retention` hook; usage decremented; younger untouched; idempotent | test |
| Rate limit | 31st upload in an hour | 429 with `Retry-After` | envelope |
| Revision | upload and delete | each bumps the Listing revision once; a concurrent editor with the old `base_revision` gets 409 | test |
| Logs and errors | any failure | no file name, bytes, key or decoder text in logs or bodies | canary test |
| Contract | `openapi.yaml`, import rules | routes, schemas, codes documented; `PIL` only in `kernel`; fresh | test |

</frozen-after-approval>

## Code Map

- `apps/kernel/{storage,images,media,models,errors,runtime,retention}.py`, `apps/kernel/migrations/`, `config/settings` (MEDIA_ROOT, upload size settings), `pyproject.toml` (Pillow), import-linter config
- `apps/listings/{models,services,selectors}.py`, `migrations/0007_images.py`
- `apps/presentation/api/listings.py` (or a `media.py` module), `config/urls.py`
- `docs/media.md`, `docs/listings.md`, `docs/runtime-and-retention.md`, `openapi.yaml`, tests

## Tasks & Acceptance

**Execution:**
- [ ] Kernel storage port and filesystem adapter; image pipeline on the executor; media assets, usage cap and retention hook
- [ ] `ListingImage`, upload/remove services, endpoints, rate limit, docs, tests

**Acceptance Criteria:**
- Given a Draft, when I upload images then JPEG, PNG and WebP up to 5 MB each and up to 10 per Listing are accepted with a pre-decode size cap, pixel and decompression-bomb limits and a type allowlist, and invalid files are rejected with a clear message.
- Given an accepted image, when it is processed it is re-encoded on the kernel runtime executor with all metadata stripped and stored under a random key behind a storage port (filesystem locally).
- Given per-account storage caps of 200 MB (Individual) and 1 GB (Agency), when uploads race the cap is checked atomically and an upload over the cap is refused.
- Given uploads that never attach to a Listing, when `run_retention` runs then orphaned files older than 24 hours are removed through a registered retention hook.

## Implementation Notes

Decisions beyond the story text: assets are generic (`kernel.MediaAsset`) so Story 2.11 reuses the pipeline and cap; the storage cap counts processed bytes; upload and delete bump the Listing revision; animated formats are refused; minimum 200 px and maximum 40 MP dimensions; the cap values are seeded policy values (admin editing in Story 2.20).

## Spec Change Log

## Review Triage Log

## Design Notes

Re-encoding (not stripping in place) is what makes metadata removal and polyglot defence robust. File first, row second, with compensating delete, keeps the database authoritative while the retention hook cleans crash windows. A kernel-level asset record lets profile pictures and logos share the cap.

## Verification

`env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check .; uv run ruff format --check .; env -u DJANGO_SETTINGS_MODULE uv run pytest -q; uv run python manage.py makemigrations --check --dry-run; uv run python manage.py spectacular --validate --file openapi.yaml` (openapi regenerated deliberately).
