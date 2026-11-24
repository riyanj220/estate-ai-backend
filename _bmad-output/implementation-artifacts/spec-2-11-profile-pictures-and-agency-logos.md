---
title: 'Story 2.11: Profile pictures and Agency logos (backend half, then frontend half)'
type: 'feature'
created: '2026-11-24'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-9-image-upload-pipeline.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-10-media-endpoint-cover-ordering.md'
  - '{project-root}/docs/media.md'
  - '{project-root}/docs/profile.md'
  - '{project-root}/docs/account-states.md'
  - '{project-root}/docs/story-checklist.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-15, AD-22, AD-23)'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Individual accounts have no profile picture and Agency accounts no logo (FR-6, FR-7, left open in Epic 1), although the image pipeline, storage cap and media-serving rules now exist.

**Approach:** Reuse the kernel media pipeline (Story 2.9) from `identity`: an owner uploads, replaces or removes their picture or logo; the stored asset is referenced from the profile (a plain id column, since `identity` owns the profile and `kernel` owns the asset). An Individual's picture is private to its owner; an Agency's logo is public through one Django endpoint, but only while the Agency account is Active. Backend first and merged, then the profile pages in `/home/riyan/Riyan/projects/estate-ai-frontend`.

## Boundaries & Constraints

**Always (backend):**
- **Storage of the reference** (`identity` migration): nullable `image_asset_id uuid` (unique when not null; plain column, no foreign key into kernel) on `IndividualProfile` and `AgencyProfile`. Only `identity.services` writes it. The asset is the SAME kernel `MediaAsset` created by the Story 2.9 pipeline (same limits: JPEG/PNG/WebP, 5 MB, bomb limits, re-encode, metadata stripped, random key, per-account storage cap counted for the owner's account, retention for unattached assets).
- **Service** `identity.services.set_profile_image(actor, data)` / `remove_profile_image(actor)` (actor first, AD-22): only the authenticated owner account of type Individual or Agency (administrators and Visitors refused; Visitor `unauthenticated`, administrator `forbidden`/not found consistent with the existing profile routes); the account state must allow profile editing exactly as the existing profile PATCH does (same capability gate: reuse it, do not invent a new one; document the matrix: Active and Email-unverified allowed, Active, Email-unverified and Suspended allowed (the same gate as the profile PATCH); Deactivated denied). Flow: process the image OUTSIDE the account lock (kernel `process_upload`), then under the account lock and the usage lock store the new asset (attached at creation), point the profile at it, delete the previous asset (row, usage, file after commit) in the same transaction; a failure after the file write removes the new file (same compensation as 2.9); removing the picture deletes the asset and nulls the reference; repeating a remove is a harmless `204`. Audit record `account.profile_image_changed` (no metadata: no file data, names or keys) in the same transaction. Domain events are not needed.
- **Endpoints** (all `no-store`, CSRF on writes, strict multipart with ONE `file` part like Story 2.9, request body cap 6 MiB enforced by the existing upload body-limit middleware and ASGI wrapper extended to these routes, rate limit seeded `profile_image_upload` 10 per hour per account):
  - Individual: `PUT /api/v1/me/profile/image` (upload or replace; `200` with the profile image object), `DELETE /api/v1/me/profile/image` (`204`), `GET /api/v1/me/profile/image/file` (the bytes, owner only; any other caller `404`; headers as the 2.10 media endpoint: stored `Content-Type`, `Cache-Control: private, no-store`, `nosniff`, `Content-Disposition: inline` without a name, `Content-Length`, the media CSP, HEAD headers-only from the asset row).
  - Agency owner: `PUT /api/v1/me/agency-profile/image`, `DELETE /api/v1/me/agency-profile/image`, `GET /api/v1/me/agency-profile/image/file` with the same rules (the owner may always read their own logo while their account state allows viewing the profile).
  - Public logo: `GET /api/v1/agencies/{agency_id}/logo` (and HEAD) where `agency_id` is the `AgencyProfile.id` (a public identifier distinct from the account id; confirm the public agency id is not the account id); served to anyone, Visitors included, ONLY while the Agency account is Active; for a Suspended, Deactivated, anonymised, Email-unverified-never-active or unknown Agency, a malformed id, or an agency without a logo the answer is the identical `404`; `Cache-Control: public, max-age=60` (short cache; documented that a suspended agency's logo may stay cached up to a minute), `nosniff`, CSP media policy, no cookies, no `Vary: Cookie` leakage, no `Set-Cookie`. An Individual's picture has NO public route (other accounts get `404` from the owner routes).
  - Unreadable file or lost asset row: uniform `404` on reads; the image object is omitted in the profile responses (null) in that case.
- **Wire**: the Individual profile GET/PATCH answers and the Agency business profile answers gain `image`: `null` or `{content_type, width, height, bytes, url}` where `url` is the owner route (`/api/v1/me/profile/image/file` or `/api/v1/me/agency-profile/image/file`) so the owner UI can render it; additionally the Agency profile response gains `logo_url` (`/api/v1/agencies/{agency_id}/logo` or null) and `agency_id` ONLY in the owner's own agency profile response (documented as public identifiers). No asset id, storage key or account id appears. Existing contract tests updated for additions only. The public agency profile endpoints that come later reuse `logo_url`.
- **Errors**: the same codes and static messages as Story 2.9 (`file`: `too_large`, `unsupported_type`, `too_large_dimensions`, `too_small`, `invalid_image`, `required`; `409 storage_cap_exceeded`; `503 busy` with `Retry-After`; `429`); non-owner account types `403 forbidden` consistent with the profile routes; OpenAPI declares every response (extend_schema for 400/401/403/404/409/429/503) and the binary responses; authorisation matrix regenerated.
- **Account lifecycle**: deactivation/anonymisation (AD-23): the existing erasure path must remove the picture/logo asset (row, usage, file) for the account (add the step in the identity erasure/deactivation code that already exists, in the same transaction; if there is no erasure path yet, document and test the helper `identity.services.purge_profile_image(account_id)` the later story will call); suspension keeps the asset but the public logo route returns `404`; reactivation makes it visible again.
- **Docs and tests**: `docs/media.md` (profile images, public logo rule and cache), `docs/profile.md`, `docs/account-states.md` (logo visibility by state), README, `openapi.yaml`, authorisation matrix; tests for every matrix row including threaded cap/replace races, rollback tests that can really fail (failure after the file write and after the reference update), no-leak canaries (names, keys, account ids) in responses and logs, and the public logo route for each account state; selector-level tests pin the access contract. Lessons from Stories 2.9/2.10: gate before parsing the body, whole-body echo checks minus the request id, deterministic tests, neutral static messages, ghost-tolerant reads (a reference to a missing asset reads as no image and is cleaned on the next write).

**Always (frontend, after the backend is merged and `npm run api:sync`):**
- Individual profile page (`/account/profile`) and Agency business profile page: a picture/logo section (own component, separate from the text form's Save): current image (from `image.url`, same-origin `<img>` with alt "Your profile picture" / "Your agency logo", lazy, with load-failure retry) or an empty placeholder (initials-free, neutral shape), a labelled file input ("Choose a picture" / "Choose a logo", `accept="image/jpeg,image/png,image/webp"`), upload/replace (one file; client-side refusal over 6 MiB as `too_large`), remove with a confirmation step, the Story 2.10 error mapping (`too_large`, `unsupported_type`, `too_large_dimensions`, `too_small`, `invalid_image`, `storage_cap_exceeded`, `busy`, 429, 401, 403 (suspended message only from session state), network/timeout), one polite live region, `aria-disabled` pending states, focus management, mutual exclusion with the text form's Save (as in 2.10), module-level busy guard and abort on unmount, bfcache re-check, no previews persisted, no file names to console or storage, axe clean at desktop and mobile in empty/populated/uploading/error states. URLs are never displayed as text. Reuse the Images lessons (shared helpers extracted from `images-section.tsx` where it reduces duplication without destabilising it: e.g. upload error mapping and the busy lock).
- Stub backend: profile image routes (upload/replace/delete/own file, same validation as the real backend: magic bytes, 5 MB, 6 MiB, modes for cap/busy/rate/401/403/404), the profile responses with `image`, the public logo route serving a tiny PNG only when the stub agency is active (mode to make it suspended). Tests per matrix row (`setInputFiles` with generated in-memory PNGs), README, plus a production-build check addition to `check:prod-listings` or a new `check:prod-profile` proving the proxy forwards the image routes (5 MB upload, binary bytes) and the public logo is cacheable by a Visitor but `private, no-store` for owner routes.

**Never:** expose an Individual's picture to anyone but the owner; serve a logo for a non-Active Agency; expose asset ids, keys or account ids; keep an unreferenced old asset after replacement; let an administrator or Visitor change a profile image; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Individual uploads a picture | valid JPEG/PNG/WebP | 200 `image` object; asset re-encoded, stored, referenced; audit record | N/A |
| Replace | second upload | reference points to the new asset; old asset row, usage and file removed once | N/A |
| Remove | DELETE | reference null, asset removed, usage decremented; repeat DELETE 204 | N/A |
| Agency uploads a logo | valid image | 200; `logo_url` set; public route serves it while Active | N/A |
| Public logo, Active agency | Visitor GET | 200 bytes, `public, max-age=60`, nosniff, CSP, no cookies | N/A |
| Public logo, other states | Suspended, Deactivated, anonymised, never-active, unknown/malformed id, no logo | identical 404 | envelope |
| Reactivation | Suspended agency reactivated | logo visible again | N/A |
| Individual picture to others | other account, admin, Visitor | owner routes give 404; no public route exists | envelope |
| Owner reads own image | owner GET `.../file` | 200 `private, no-store` bytes; HEAD headers only | N/A |
| Wrong account type | Agency on the Individual route and vice versa; administrator | 403 `forbidden` like the profile routes | envelope |
| Account states | Active / Email-unverified allowed; Suspended / Deactivated denied | as the profile PATCH matrix | envelope |
| Image validation | too large, wrong type, bomb, tiny, corrupt, no file, two files | codes as 2.9 under `file`; nothing stored | envelope |
| Storage cap / busy / rate | over cap; executor full; 11th upload in an hour | 409 / 503 + Retry-After / 429 | envelope |
| Failure after file write | DB failure | file removed, usage unchanged, old image intact | test |
| Concurrency | two replaces; replace racing a cap | exactly one reference, no leaked asset, cap never exceeded | test |
| Ghost reference | asset row missing | reads: image null / 404; next write cleans | test |
| Erasure / deactivation | account anonymised | asset row, usage and file removed | test |
| No leaks | any failure | no names, keys, asset ids, account ids in bodies or logs | canary test |
| Contract | `openapi.yaml`, matrix | routes, schemas, binary responses documented; fresh | test |
| Frontend: populated / empty | profile with and without image | image or placeholder; labelled controls | N/A |
| Frontend: upload/replace/remove | actions | status, errors mapped, focus kept, remove asks first | N/A |
| Frontend: exclusion and sync | text form and image actions | each blocks the other; no spurious conflicts | N/A |
| Frontend: privacy and a11y | any state | no storage/console leak, no object URLs kept; axe clean desktop and mobile; keyboard-only works | test |

</frozen-after-approval>

## Code Map

- Backend: `apps/identity/{models,services/,selectors}.py`, migration, `apps/kernel/{media,upload_limit}.py` (extend body limits), `apps/presentation/api/{profile,agency,...}.py` (new image views), `config/urls.py`, kernel policy seeding migration (`profile_image_upload`), docs, `openapi.yaml`, tests
- Frontend: `src/app/(app)/account/profile/*`, agency business profile page, shared image helpers, `src/lib/*`, `tests/e2e/{stub-backend.mjs,profile-image.spec.ts}`, scripts, README

## Tasks & Acceptance

**Execution:**
- [ ] Backend: reference columns, set/remove services, owner and public endpoints, limits, erasure hook, docs, tests
- [ ] Frontend: picture and logo sections on both profile pages, stub, tests, prod check, README

**Acceptance Criteria:**
- Given the Individual profile or the Agency business profile, when I upload a picture or logo then it passes the same pipeline and limits as Story 2.9 and only the owning account can change it.
- Given an Agency logo, when a Visitor requests it then it is served only while the Agency account is Active; a Suspended or Deactivated Agency's logo returns not-found.
- Given an Individual profile picture, when another account requests it then it returns not-found (it is private to its owner).

## Implementation Notes

Decisions beyond the story text: the public logo identifier is the `AgencyProfile.id` (not the account id); the logo cache is 60 seconds; profile images count against the owner's storage cap; replacement deletes the previous asset in the same transaction.

## Spec Change Log

## Review Triage Log

Backend half (three reviewers). Decision recorded: Suspended accounts may change their profile image, because the spec said to reuse the existing profile PATCH gate and that gate only refuses Deactivated (docs/profile.md and its tests say Suspended accounts may edit their profile); the matrix row saying "Suspended denied" was wrong and is corrected. Patched: the PUT answer is built from the stored asset (a race could 500 after commit); account-type refusals no longer burn the upload quota; HEAD is 404 exactly when GET is (lost file); the public logo route's no-session rule is pinned by behavioural tests; a conftest fixture re-seeds the rate-limit policies after transactional tests (fixes a pre-existing test-order failure); body-limit docs and ASGI tests for both profile PUT routes. Accepted and documented: no rate limit or ETag on the public logo, a suspended or replaced logo may stay cached up to 60 seconds, a lost asset row leaves its bytes in the usage counter (needs a maintenance recompute), a resolver-level 404 under the logo path can still carry the CSRF cookie. One parallel admin-counter test failed once in a full run and passed on three isolated runs and a second full run (thread contention, known class). Verification: lint-imports 14 kept, ruff, pytest 4246 passed, no migration drift, openapi regenerated.

## Design Notes

Reusing the kernel pipeline and asset record means caps, retention and metadata stripping are inherited, and an unreferenced asset can only exist inside the 24 hour retention window. The public logo is the only unauthenticated media route; keeping it a separate narrow endpoint keeps the Draft media rules unchanged.

## Verification

**Backend:** `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check .; uv run ruff format --check .; env -u DJANGO_SETTINGS_MODULE uv run pytest -q; uv run python manage.py makemigrations --check --dry-run; uv run python manage.py spectacular --validate --file openapi.yaml` (openapi regenerated deliberately).
**Frontend:** `npm run lint -- --max-warnings 0 && npm run typecheck && npm run build && npm run api:check && npm run check:prod-admin && npm run check:prod-listings && PW_CHANNEL=chrome npx playwright test` (full run in the background; never two at once).
