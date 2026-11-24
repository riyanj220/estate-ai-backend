# Media: image upload pipeline, storage and serving (Stories 2.9 and 2.10)

Generic, reusable media code lives in `apps/kernel` (Story 2.11 reuses it for profile pictures and logos); the Listing
side lives in `apps/listings` (`ListingImage`, `upload_image`, `remove_image`, `set_image_order`). Story 2.9 stored
images and served nothing; Story 2.10 adds the one media endpoint (see "Serving images" below). The storage port still
produces no URL: a file is read only through that endpoint.

## Storage port (`kernel.storage`)

Four operations on opaque keys: `put(key, data)`, `get(key) -> bytes`, `delete(key)` (idempotent) and `exists(key)`.
The filesystem adapter is rooted at `MEDIA_ROOT` (absolute path; default `<project>/media`, git-ignored; tests use a
temporary directory per test). Keep it outside any web-served directory; only the media endpoint (below) reads it, after an access check.

- Keys are `secrets.token_urlsafe(32)` (43 URL-safe characters), never derived from a file name, owner or Listing.
  Files sit under `MEDIA_ROOT/<key[:2]>/<key[2:4]>/<key>`. The adapter refuses (`InvalidKey`) any key that is not that
  exact format, so a key can never traverse out of the root.
- Writes are atomic: a temp file in the same directory, `fsync`, then `os.replace`; files are mode `0600`, directories
  `0700`. A failed write leaves no temp file.
- A maintenance listing (`iter_old_keys`, `remove_old_temp_files`) lets retention find files that have no row. It is not
  part of serving.

## Image pipeline (`kernel.images`, the only module that imports Pillow)

`process_image(data) -> ProcessedImage(data, content_type, width, height)`. Pillow is imported only in `kernel`
(import-linter contract "Pillow is imported only inside kernel"; `PIL` is a root package of the contracts).

Order of checks and the field codes (`validation_error`, field `file`; messages are static, in
`kernel.errors.FIELD_MESSAGES`, and never echo a name, a value or decoder text):

| Step | Rule | Code |
|---|---|---|
| 1 | At most 5 MB (5,242,880 bytes), checked on the byte length BEFORE any decoding | `too_large` |
| 2 | Type from the decoded format, never the file name or declared type: JPEG, PNG or WebP. GIF, SVG, TIFF, BMP, ICO, HEIC, PDF, HTML and any animated image (APNG, animated WebP) are refused | `unsupported_type` |
| 3 | From the header, before the full decode: each side at most 10,000 px and at most 40 megapixels; Pillow's bomb warning is an error and `Image.MAX_IMAGE_PIXELS` is set explicitly to 40,000,000 | `too_large_dimensions` |
| 3 | Each side at least 200 px | `too_small` |
| 4 | Corrupt, truncated or unrecognised data | `invalid_image` |
| 5 | Decode fully, apply the EXIF orientation, convert to RGB (a PNG with transparency keeps alpha as PNG; transparent WebP is flattened onto white), copy into a fresh bitmap, downscale so the long side is at most 2,048 px (never upscale) and ENCODE AGAIN (JPEG quality 85 progressive; PNG optimised; WebP quality 85) | |

Re-encoding from a fresh bitmap is what drops all metadata (EXIF including GPS, XMP, comments, ICC profile after the
sRGB conversion, PNG text chunks) and any trailing bytes, so a JPEG with a ZIP appended is stored clean. The output is
never the input bytes. A test uploads an image carrying EXIF GPS, a comment, an ICC profile and a ZIP and asserts none of
them is in the stored bytes (byte search and a Pillow re-read).

The work runs on the bounded runtime executor (`runtime.submit_heavy`, AD-14) and the caller waits at most 20 seconds.
A full executor or a timeout is `images.Busy` (`503 busy` with `Retry-After`: retry). A synchronous DRF view holds its
request thread while it waits (the ASGI thread-sensitivity note in `docs/runtime-and-retention.md` applies): the wait is
bounded by that timeout and by the number of heavy slots. A timed-out job keeps its slot until it finishes.

## Media assets and the storage cap (`kernel.media`)

- `MediaAsset(id, owner_account_id, storage_key, content_type, bytes, width, height, created_at, attached_at)`: one
  row per stored file; no file name, no original bytes. `owner_account_id` is a plain UUID (the kernel never points at
  another app's table). `attached_at` is null until a feature takes the asset.
- `StorageUsage(account_id, bytes_used)`: the bytes stored per account, counted on the PROCESSED size.
- `media.store_image(actor, data, account_type=...)` (or `process_upload` then `store_processed`, which `upload_image`
  uses so the slow work never runs under a lock): in ONE transaction it locks the account's usage row
  (`select_for_update`, the row is created on first use), refuses `storage_cap_exceeded` when `bytes_used + size` would
  pass the cap, writes the file to the store BEFORE the transaction commits, inserts the row and increments the usage.
  If the transaction fails the file is removed again; a caller that holds its own transaction open discards the file
  itself on failure (`media.discard_file`). A crash between the write and the commit leaves a file with no row, which
  retention removes.
- Only a signed-in account actor (`ActorKind.USER`) may store: a Visitor is `unauthenticated`, an administrator or the
  system `forbidden`.
- **Caps** are seeded cap policies (rows of `kernel_ratelimitpolicy` with no window, like `open_drafts`):
  `storage_cap_bytes_individual` = 209,715,200 (200 MB) and `storage_cap_bytes_agency` = 1,073,741,824 (1 GB). The kernel
  cannot read an account's type, so the caller passes it and the scope is picked from it. Administrators edit the
  values in Story 2.20; nothing else hardcodes them. A windowed or missing policy is `ImproperlyConfigured`.
- `media.delete_asset(actor, asset_id)`: the row goes and the usage is given back (never below zero) in one
  transaction, and the file is deleted after commit (a failing delete is logged by class and never stops anything). An
  asset the actor's account does not own is `not_found`.
- Lock order: account, then Listing, then storage usage, everywhere.

## Orphan retention (`kernel.media_orphans`)

Registered with `kernel.retention` (`run_retention`). Each run removes at most 500 of each:

1. `MediaAsset` rows with `attached_at` null and `created_at` more than 24 hours ago: the row goes, the usage is
   decremented and the file is deleted after commit;
2. stored files that no row refers to and that were last written more than 24 hours ago (found by walking the store in
   batches), and leftover temp files older than 24 hours.

Files younger than 24 hours are never touched, so an upload in flight is safe. Deletions are idempotent and a failure on
one file never stops the rest (the leftover is found as a stray file on the next run). `--dry-run` reports the counts.

## Listing images (`listings`)

See `docs/listings.md`. Summary: `ListingImage(listing, asset_id, position, is_cover)`, at most 10 per Listing
(`too_many_images`, counted under the Listing row lock), `POST /api/v1/listings/{id}/images` (multipart, one `file`
part), `PATCH /api/v1/listings/{id}/images` (reorder and cover), `DELETE /api/v1/listings/{id}/images/{image_id}`,
`GET /api/v1/listings/{id}/images/{image_id}/file` (the media endpoint). Rate limit `image_upload` (30 per hour per
account; it counts uploads only).

### Cover and order (Story 2.10)

- `position` is 0-based and contiguous per Listing (0..n-1), maintained by the service under the Listing row lock. The
  `(listing, position)` unique constraint is `DEFERRABLE INITIALLY DEFERRED`, so a reorder can rewrite positions in any
  order inside one transaction; a duplicate still fails at commit. A `position >= 0` CHECK remains.
- `is_cover` is a boolean with a partial unique index (`UNIQUE (listing_id) WHERE is_cover`): at most one cover per
  Listing. A partial index cannot be deferred, so the service clears the old cover before it sets the new one.
- The first image uploaded to a Listing is the cover (position 0); a later upload is appended and never changes the
  cover. Removing the cover promotes the image with the lowest remaining position; removing any image renumbers the rest
  contiguously; the last image removed leaves no cover.
- Migration `0008_cover` renumbers Story 2.9's 1-based, gappy positions to 0..n-1 (in the old order) and makes the first
  image of each Listing the cover; it is reversible (positions return to 1..n).
- Reorder never touches files, assets or the storage usage.
- **Ghost links.** A `ListingImage` whose `MediaAsset` row is gone is a ghost: the Listing resource omits it. Every
  mutating image service (`upload_image`, `remove_image`, `set_image_order`) first repairs the set under the Listing
  lock: ghosts are deleted, positions renumbered 0..n-1 and exactly one cover ensured. The repair rides on that call's
  own revision write (a reorder that changes nothing else but finds a repair to make counts as a change and records
  `"repaired": true`). Ghosts are not counted towards the 10-image limit. Upload places the new image at
  `max(position) + 1`.
- `PATCH .../images` has a 64 KiB body cap (`MAX_IMAGE_ORDER_BODY_BYTES`, enforced by the same middleware and ASGI
  wrapper as the upload cap; refusal `400 validation_error` with `fields.order` `invalid`), an `order` longer than 10 is
  refused before anything else, and the `image_order` rate limit (120 per hour per account; `docs/rate-limits.md`).

## Serving images (Story 2.10)

`GET /api/v1/listings/{listing_id}/images/{image_id}/file` (also `HEAD`) is THE media endpoint: the only place an image
is served, so access is decided at request time and never by a URL being secret. The view is a thin shell over the seam
`listings.selectors.image_for_serving(actor, listing_id, image_id)`, which returns the stored bytes and content type or
raises `not_found`; Story 2.12 rewires that one function to the single visibility policy (public Published listings)
and nothing else changes.

Rules in this story: only a Draft's image, only to its owning, non-administrator account actor holding `view_drafts`
(Suspended and Email-unverified owners may view their own images; a Deactivated owner cannot). Everyone else gets the
identical `404 not_found` envelope: another account, an administrator, a Visitor (404, never 401, so existence is never
confirmed), unknown or malformed ids (only canonical lowercase hyphenated UUIDs), another Listing's image id, a Listing
that is not a Draft, and a lost row or file. The route needs no CSRF token (read only). It has **no rate limit** and each `GET` reads the whole file (up to 5 MB)
into memory; a limit or a streaming read can be added behind the same seam. The authorisation matrix lists the route as
`public` because the owner check is inside `image_for_serving`, not in a route permission class.

The `200` response streams the stored, re-encoded bytes (never the upload) with:

| Header | Value |
|---|---|
| `Content-Type` | the stored `image/jpeg`, `image/png` or `image/webp` (the only use of the stored type) |
| `Content-Length` | the stored size |
| `Cache-Control` | `private, no-store` (a shared cache must never keep a Draft image); errors are `no-store` |
| `X-Content-Type-Options` | `nosniff` |
| `Content-Disposition` | `inline` (no file name) |
| `Content-Security-Policy` | `default-src 'none'; sandbox` (the security-headers middleware keeps a CSP a response already carries) |

No cookie is set and the request never reveals the storage key or a path. `HEAD` returns the same headers and no body; it decides access identically but reads no file (`Content-Length` is the
asset row's stored size, so a lost file shows only on `GET`).
`Range` is ignored (always `200` with the whole file, no `Accept-Ranges`), conditional headers are ignored and there is
no `ETag`; `Accept` is ignored for the image (errors are JSON). Images are at most about 2,048 px and 5 MB, so the body
is read whole; a streaming or `X-Accel-Redirect` variant can replace that behind the same seam if needed.
`kernel.media.read_asset` (bytes and row, no access check) and `assets_by_id` are the kernel's read side; the access
decision lives in `listings`.

## Security properties

- The `Content-Type` header and the file name are never trusted, stored or logged; no file name is kept anywhere.
- Stored files are never executed or served from disk by a web server; only the media endpoint serves them, after the
  access check, and no response carries a storage key or a filesystem path (the Listing's `media[].url` is the
  endpoint's relative path).
- Logs and error bodies never contain file data, names, storage keys or decoder text (static messages, class-only logs).
- The request body of the upload route is capped at 6 MiB, in layers and for that route only (`kernel.upload_limit`;
  every other route keeps Django's defaults, `DATA_UPLOAD_MAX_MEMORY_SIZE` included): `BodyLimitASGI` (wrapped around
  the application in `config/asgi.py`) refuses a declared `Content-Length` above the cap without reading a byte, and
  counts the bytes of a chunked or mis-declared body as they arrive, stopping at the cap, BEFORE Django buffers
  anything; `BodySizeMiddleware` (ahead of the CSRF check, which would otherwise parse a multipart body when the token
  header is missing) flags an absent, malformed or oversize length and zeroes it. The refusal is `400 validation_error`
  with `file: too_large` in the standard envelope (the ASGI refusal is sent before authentication, so it does not
  reveal whether a Listing exists). `FILE_UPLOAD_MAX_MEMORY_SIZE` is aligned and only the in-memory upload handler is
  enabled, so an upload never spills to a temp file. A reverse proxy in front of the application must cap the body too
  (for example `client_max_body_size 6m` in nginx): the application bounds its own buffering, not the socket.
- The ownership and capability checks run BEFORE the body is parsed or validated: a non-owner always gets 404 and a
  Suspended owner 403, whatever the body.
- Shard directories are created explicitly with mode `0700` (the process umask is overridden with `chmod`), files are
  `0600`, and the parent directory is `fsync`ed after the rename so a stored file survives a crash.
- Pillow needs no system libraries (the wheels bundle libjpeg, libpng, libwebp and Little CMS).

## Known limitations and follow-ups

- **Sync view.** `POST .../images` is a synchronous DRF view that waits up to 20 seconds on the executor future. Under
  ASGI a sync view occupies the thread-sensitive thread, so slow image work delays other synchronous requests of that
  process for as long as it runs. Recorded limitation: move the upload to an async view (using `runtime.run_heavy`)
  when the first async, DRF-free upload view is introduced. A job that times out keeps its heavy slot until it
  finishes (it cannot be killed), so repeated timeouts show up as `busy`.
- **Memory.** Decodes are limited to two at a time (a dedicated semaphore, inside the heavy slot); a 40 megapixel image
  is hundreds of megabytes while it is decoded. 16-bit greyscale is scaled to 8 bits, a multi-picture JPEG (MPO) is read
  as its first frame, and palette, greyscale and RGB colour-key transparency is kept as alpha in a PNG.
- **TODO (Story 2.17).** Attached assets are NOT reclaimed when a Listing is deleted: nothing here removes
  `ListingImage` rows or files on a Listing delete. Story 2.17 must call `media.delete_asset` for every image (and so
  give the bytes back) in the same transaction as the delete.
- **Retention walks the whole store** to find files with no row (batched, at most 500 removals per run). That is fine
  at MVP scale; a cursor would be needed for very large stores.
