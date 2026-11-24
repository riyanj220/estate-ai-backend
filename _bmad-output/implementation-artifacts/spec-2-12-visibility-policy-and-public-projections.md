---
title: 'Story 2.12: Visibility policy and public projections (backend only)'
type: 'feature'
created: '2026-11-24'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-10-media-endpoint-cover-ordering.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-11-profile-pictures-and-agency-logos.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md (AD-5, AD-15, AD-22, AD-24, AD-26)'
  - '{project-root}/docs/media.md'
  - '{project-root}/docs/listings.md'
  - '{project-root}/docs/authorisation-matrix.md'
  - '{project-root}/docs/story-checklist.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Only Drafts exist, so nothing can be public yet, but every later story (publish, browse, favourites, chat, media) needs ONE answer to "may this viewer see this Listing" and ONE public shape that can never carry the private address or phone.

**Approach:** Backend only. Add the `published` status and the `restricted` flag (set only by fixtures here; moderation sets it in a later epic), implement `listings.policy.can_view(viewer, listing)` and `listings.selectors.public_listings()` as the only implementations of visibility, add a frozen public projection type built only from public registry fields, and rewire the media seam `image_for_serving` to the policy so images of a publicly visible Listing are served to anyone with short cache headers while images of any other Listing go only to the owner. No public listing route yet (discovery and detail come later); the projection is reached through a selector.

## Boundaries & Constraints

**Always:**
- **Statuses**: `ListingStatus` gains `PUBLISHED = "published"` (Draft stays; later stories add more). `Visibility` gains `PUBLIC`; `visibility_of(status)` returns `public` for published and `private` otherwise (the owner-facing `visibility` field of the resource keeps working). `EDITABLE_STATUSES` is unchanged (still Draft only: publishing and editing a Published listing are Story 2.15). The status CHECK constraint is regenerated (migration `0009_visibility`). Add `restricted boolean not null default false` to `PropertyListing` (same migration). No service sets `published` or `restricted` in this story: fixtures and tests create such rows directly through the model (a test helper in `tests/`), and the docs say so.
- **The one policy (AD-5)**: `listings.policy.can_view(viewer: Actor, listing) -> bool` and `listings.selectors.public_listings()` are the ONLY implementations. Public iff `status == published` AND NOT `restricted` AND the owner is an Active eligible owner (`identity.selectors.is_active_owner`). `can_view` is true for a viewer when the Listing is public, or when the viewer is the owning account (signed-in, non-administrator account actor whose account is not Deactivated; the same owner rule `image_for_serving` uses today for Drafts, so Suspended and Email-unverified owners keep seeing their own). Everyone else (Visitor, another account, an administrator) gets False for any non-public Listing. `public_listings()` is a lazy, internal-to-`listings` queryset of `PropertyListing` rows filtered by the same predicate; it is never returned across the app boundary (other apps get views, AD-3). Both are built from one shared predicate helper so they cannot drift; a test asserts `can_view` for a Visitor agrees with membership in `public_listings()` for every matrix cell. If the identity layering forbids a subquery over its table, add a narrow identity selector (for example returning the ids of Active owners as a lazy values queryset) and keep the import-linter contracts green; do not duplicate the Active-owner rule.
- **Not-found, never forbidden**: `selectors.public_listing(viewer, listing_id) -> PublicListingView` returns the projection when `can_view` allows and the Listing is public, and raises `DomainError("not_found")` for every other cell (unknown id, malformed id, draft, restricted, owner suspended/deactivated, any viewer). It does not confirm existence. (The owner-facing `get_listing` is unchanged.)
- **Public projection (AD-5, AD-24, AD-26)**: a frozen dataclass `PublicListingView` (and `PublicImageView`) in `listings.selectors` or a `listings.projections` module, built field by field (never `__dict__`, never a model instance): `id`, `purpose`, `category`, `subtype`, `title`, `description`, `fields` (only registry fields whose class is `public`; private-class fields, when any exist later, are never copied), `media` (ordered; each `{id, position, is_cover, content_type, width, height, url}` where `url` is the media endpoint path; no asset id, storage key, `bytes` is allowed but not required), `registry_version`. It carries NO `owner_account_id`, `status`, `restricted`, provenance, revision, revision history, private address or phone. A guard test walks the dataclass fields and the registry: it fails if a registry field of class `private` could appear, if an owner/account/asset/storage name appears in the projection's field names or its serialised form, and it must be possible for the test to fail (assert on a registry fixture that adds a private field).
- **Media seam (AD-15)**: rewire `listings.selectors.image_for_serving` (the endpoint file does not change except the cache header, below): `not_found` unless the image belongs to that Listing AND (`can_view` allows: public Listing for any viewer including Visitors, or the owner for their own non-public Listing including Drafts). A Visitor and a non-owner get the same 404 as for unknown ids. An Administrator gets 404 for a non-public Listing (admin reads go through the audited private-read path, not here); an Administrator MAY fetch images of a public Listing like any Visitor. An owner whose account is Suspended: other viewers get 404 for all of that owner's Listings and images until reactivation (then they are visible again); the owner still sees their own. `ServedImage` gains `public: bool` (true only when the Listing is publicly visible at that moment). The endpoint sets `Cache-Control: public, max-age=60` and sets NO cookie and NO `Vary: Cookie` for a `public` image (the same cookie-free guarantees as the public logo, including for a request that carries a session cookie, since the answer does not depend on who asks), and keeps `private, no-store` for owner-only images; `nosniff`, CSP sandbox, inline, no file name, HEAD, no Range/ETag unchanged. HEAD decides exactly like GET and the two never disagree.
- **Where it applies**: every place that reads a Listing for someone other than the owner must start from `public_listings()` / `can_view`; add a repository test (an AST or grep-style guard, like the existing import contracts) that no module outside `listings.policy`/`listings.selectors` filters `PropertyListing` by `status == published` or reimplements the predicate (assert on a deliberately violating fixture so the guard can fail).
- **Matrix test (table-driven)**: statuses {draft, published} × restricted {false, true} × owner state {Active, Email-unverified-Active, Suspended, Deactivated} × viewer {Visitor, other Active account, owner, other account's Administrator} → for `can_view`, `public_listings()` membership, `public_listing`, and image serving (GET and HEAD, plus the response headers). Expected: public cell = published ∧ ¬restricted ∧ owner Active (Email-unverified is an Active-state account only if the existing account model counts it so; read `identity` and encode exactly what `is_active_owner` returns, and state the result in the spec Verification notes). Owner sees own non-public (not when Deactivated). Everyone else 404.
- **Docs and tests**: `docs/visibility.md` (policy, projection, media rules, how later stories must use them), update `docs/media.md`, `docs/listings.md`, `docs/authorisation-matrix.md`; `openapi.yaml` regenerated (the media endpoint's description and headers change; confirm only that changes) and committed; tests for the matrix, projection guards, the migration (constraint regenerated, default false, drift check), headers (public vs private, no Set-Cookie, no Vary: Cookie even when the request has a session cookie, HEAD parity), a Listing that becomes non-public while an image is requested, suspended-then-reactivated visibility, and no private data in responses or logs (whole-body checks minus the request id). Lessons from earlier stories apply (no vacuous assertions, static neutral messages, tests that can really fail, `_reseed_flushed_policies` fixture exists).

**Never:** let any code path other than the two implementations decide visibility; return a model instance across the app boundary; include the owner id, status, restricted flag, provenance or any private-class field in a public projection; set `published` or `restricted` from a service or route in this story; serve a non-public image to anyone but its owner; set a cookie or `Vary: Cookie` on a public image; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Public listing | published, not restricted, owner Active | `can_view` true for every viewer; in `public_listings()`; `public_listing` returns the projection | N/A |
| Draft | any owner state | not public; owner (non-Deactivated) can_view; others not | not_found |
| Restricted | published, restricted | not public; owner can_view; others not | not_found |
| Owner Suspended / Deactivated | published, not restricted | not public to others until reactivation; Suspended owner still sees own; Deactivated owner not | not_found |
| Visitor / other account / Administrator | non-public listing | False / not_found, identical to unknown id | N/A |
| Image of a public listing | any viewer incl. Visitor, GET and HEAD | 200 bytes, `public, max-age=60`, nosniff, CSP sandbox, no cookie, no `Vary: Cookie` | N/A |
| Image of a non-public listing | owner | 200, `private, no-store` | N/A |
| Image of a non-public listing | anyone else | identical 404 | envelope |
| Image of another listing / malformed ids | any | identical 404 | envelope |
| Projection | public listing | only public fields; no owner/status/restricted/provenance/private data | guard test |
| Predicate drift | code outside the two implementations filters by published | guard test fails | test |

## Code Map

- `apps/listings/lifecycle.py` (statuses, visibility), `models.py` + migration `0009_visibility.py`, new `policy.py`, `selectors.py` (`public_listings`, `public_listing`, projections, `image_for_serving`, `ServedImage.public`), `apps/identity/selectors.py` (Active-owner helper if needed), `apps/presentation/api/listing_images.py` (cache header), `tests/` (matrix, helpers, guards), `docs/`, `openapi.yaml`, `importlinter` config if a contract needs adjusting.

## Spec Change Log

## Review Triage Log

Three reviewers, one patch batch of 11 items. Patched: `VisibilityEnum` kept as its own OpenAPI component (labelled choices plus an enum-name override; `FieldClassEnum` unchanged; a test pins the ref); `image_for_serving` decides public once; `public_listing` no longer re-queries; owner access is explicit over draft and published (an unknown status is refused for everyone, tested with the CHECK dropped); public images clear cookies and `Vary: Cookie` explicitly and are mutation-tested; projection tests assert positive public fields and an end-to-end private-class omission (mutation-checked), `public_fields` fails loudly on an unregistered name; the single-source guard also forbids importing `PropertyListing`/`ListingImage` outside `apps/listings` and catches dynamic and raw-SQL spellings; the owner-facing `visibility` is derived from the policy (public only while really public; batched per page); index `listings_public_idx`; populated-table migration test. Accepted: image takedown can lag up to 60 s in caches (documented); `visibility` OpenAPI description labels are uppercase (values unchanged); no public HTTP route yet (discovery later); `public_listing` keeps an unused `viewer` argument. Verification: lint-imports 14 kept, ruff, pytest 4387 passed 2 skipped, no migration drift, openapi fresh.

## Design Notes

One predicate helper feeds both the queryset and the per-object check, so Discovery, detail, media, favourites, chat and counts can only ever differ by the filters they add on top. The projection is deliberately a separate type from `ListingView` (the owner's wire shape): the compiler and the guard test, not review discipline, keep owner-only data out of public responses.

## Verification

`env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check .; uv run ruff format --check .; env -u DJANGO_SETTINGS_MODULE uv run pytest -q; uv run python manage.py makemigrations --check --dry-run; uv run python manage.py spectacular --validate --file openapi.yaml` (openapi regenerated deliberately; only the media endpoint description/headers may change).

### Implementation notes (Email-unverified versus `is_active_owner`)

- `identity.selectors.is_active_owner` requires `AccountState.ACTIVE`. The state is derived (`identity.state.derive_state`: Deactivated, then Suspended, then **Email-unverified**, then Active), so Email-unverified is a separate state and NOT an Active owner. Matrix result: published, not restricted, Email-unverified owner is not public (not in `public_listings()`, `public_listing` and the media endpoint refuse others), while the unverified owner still sees their own (`can_view` true, `private, no-store`). The spec's phrase "Email-unverified-Active" was resolved this way.
- `identity.selectors.active_owner_account_ids()` (a lazy `values("id")` queryset, verified and not suspended and not deactivated and Individual or Agency) feeds the subquery in `listings.policy.public_q()`; a test asserts it agrees with `is_active_owner` for every account type and state. Listings cannot import `identity.models`, so the filter lives in identity.
- `policy.is_public(listing)` re-reads the row through `public_q()` (no stale-instance drift); `can_view` = `is_public or is_owner`.
- The old test that monkeypatched a non-Draft status (`test_a_listing_that_is_not_a_draft_*`) was removed: its premise (Published is refused to the owner) no longer holds; the matrix tests replace it. The status CHECK test now uses `archived` as the invalid value.
