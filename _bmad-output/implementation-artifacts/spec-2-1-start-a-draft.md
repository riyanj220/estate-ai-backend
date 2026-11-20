---
title: 'Story 2.1: Start a Draft with purpose, Category and Subtype (backend half)'
type: 'feature'
created: '2026-11-20'
status: 'review'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/docs/account-states.md'
  - '{project-root}/docs/story-checklist.md'
  - '{project-root}/docs/audit.md'
  - '{project-root}/docs/rate-limits.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** There is no Listing at all: no model, no Field registry, no way for an owner to start a Draft, and no shared definition of what fields exist.

**Approach:** Create the `listings` app's first aggregate (`PropertyListing` with revision 1 and a `ListingRevision` record) and the `catalog` Field registry (YAML, versioned, validated at start-up, served at `GET /api/v1/registry`), then let a signed-in Individual or Agency owner start a Draft by choosing purpose (sale or rent), Category and a Subtype that belongs to it. The registry initially contains only the three fields introduced now (purpose, category, subtype); later stories add fields and bump its version. This spec is the backend half; the "Create listing" page and the Drafts list are a second spec after the backend merges.

## Boundaries & Constraints

**Always:**
- **Field registry** in `catalog`: a YAML file `apps/catalog/registry/fields.yaml` (loaded through a package-data-safe path) with `version: <int>`, a `taxonomy` section (code-defined Categories and Subtypes) and a `fields` list. Taxonomy ids (snake_case): categories `house` (subtype `house`), `apartment` (subtype `apartment`), `plot` (subtypes `residential_plot`, `commercial_plot`, `plot_file` where Plot File is a distinct Subtype of Plot), `commercial` (subtypes `shop`, `office`); purposes `sale`, `rent`. Each field has: `id` (stable snake_case), `type` (`enum`, `text`, `money`, `integer`, `decimal`, `boolean` as needed; only `enum` is used now), `requirement` (`required_for_publication`, `recommended`, `optional`), `applicability` (purposes, categories, subtypes it applies to; empty means all), `allowed_values` (for enums, derived from the taxonomy for category and subtype), `class` (`public` or `private`) and flags `searchable`, `sortable`, `ai_extractable`, `valuation_input`, `quota_identity`. Now: `purpose` (required for publication, public, quota identity), `category` (same), `subtype` (same), none searchable or sortable yet (later stories flag them).
- **Loader** (`apps/catalog/registry.py`, a pure module plus a cached accessor) validates the file at start-up (`CatalogConfig.ready`) and raises `ImproperlyConfigured` with a one-line reason on any problem: unknown keys, duplicate ids, a Subtype listed under two Categories, an enum whose allowed values do not match the taxonomy, a missing or non-integer version, and the hard rule that a private-class field flagged `searchable`, `sortable` or `valuation_input` is rejected (tested with a bad YAML fixture per rule). Public accessors return immutable values (frozen dataclasses): `registry_version()`, `field(id)`, `fields()`, `categories()`, `subtypes_of(category)`, `valid_subtype(category, subtype)`.
- **Registry lock**: `apps/catalog/registry/fields.lock` holds `version` and the SHA-256 of the canonical YAML; a test fails when the YAML changed without the lock (and so without a version bump) with a message telling the developer to bump the version and run `scripts/registry_lock.py` (a small script that rewrites the lock). Initial version is 1.
- **`GET /api/v1/registry`**: public (`AllowPublic`), returns `{version, taxonomy: {categories: [{id, subtypes: [...]}], purposes: [...]}, fields: [...]}` exactly as defined (no internal paths), with `Cache-Control: public, max-age=300` and a strong `ETag` derived from the version and content hash (honour `If-None-Match` with `304`). OpenAPI enums for purpose, category and subtype in serializers are generated from the registry, never hand-copied.
- **Listing model** in `listings` (`PropertyListing`, written only by `listings.services`): `id` UUID pk, `owner_account_id` UUID (plain column, indexed, no foreign key into identity), `status` (`draft` for now; the lifecycle enum is code-defined in `listings` and later stories extend it), `revision` integer starting at 1, `registry_version` integer, `purpose`, `category`, `subtype` (the registry fields stored as columns with CHECK constraints matching the taxonomy values), `created_at`, `updated_at`; indexes for (`owner_account_id`, `status`, `-created_at`). `ListingRevision` (`listing` FK within `listings`, `revision`, `origin` `manual`, `actor_account_id` UUID, `changes` JSON of the registry fields set at that revision, `created_at`, unique (`listing`, `revision`)); the creation writes revision 1 with the three fields. The listing and revision rows are inserted in one transaction with the audit record.
- **Create service** `listings.services.create_draft(actor, purpose, category, subtype) -> ListingView` (actor first). Rules in this order: the actor must be signed in (`unauthenticated` for Visitors); the account must hold the `edit_drafts` capability through `identity.policy.can` (Active and Email-unverified pass; Suspended and Deactivated are refused according to the FR-74 matrix; administrators are refused: they are not eligible owners and never hold the capability); remove `edit_drafts` and `view_drafts` from `PENDING_CAPABILITIES` in this story with their own tests; validate the body against the registry (`purpose` in the registry purposes, `category` a known Category, `subtype` belonging to the chosen Category; every failing field reported at once with stable codes `required`, `invalid`, `invalid_choice`, and `subtype_not_in_category`, values never echoed); then, inside one transaction, take `identity.selectors.lock_account(actor.account_id)` first and enforce the open-Draft cap: the seeded policy `open_drafts` (20, no window) is a CAP, counted over the owner's Listings in status `draft`; at the cap the request is refused with the standard `429 throttled` envelope (a `RateLimited`-style domain error carrying a `Retry-After` of a sensible fixed value is NOT used for caps: use the existing cap mechanism if one exists in `kernel.ratelimit`, otherwise raise the standard throttled error without `Retry-After` and document it); then insert the listing (owner is the actor's account; Agency-created Drafts belong to the Agency account, shared by all its employees) with `revision = 1` and the current `registry_version`, write revision 1, write the audit record `listing.draft_created` (target the listing id; enumerated metadata `purpose`, `category`, `subtype` only), and return the view. Concurrency: two simultaneous creations at 19 drafts produce exactly one success and one 429 (threaded test).
- **Read services and selectors**: `listings.selectors.get_owned_listing(actor, listing_id)` returns the owner's listing view; any other account, a Visitor or an administrator gets not-found (never forbidden, so existence is not confirmed); `listings.selectors.list_owned(actor, status=None, cursor, limit)` returns the owner's own listings newest first. Views are plain frozen dataclasses with the wire shape below; models never leave the app.
- **Wire shape** (AD-24), asserted by a contract test: `{id, status, visibility, revision, registry_version, fields: {purpose, category, subtype}, media: [], amenities: []}`; `visibility` is `"private"` for Drafts (a code-defined enum extended by Story 2.12); `fields` is keyed by flat registry ids and returns `null` for unknown; no `owner_account_id` or any internal column is returned.
- **API** (`apps/presentation/api/listings.py`): `POST /api/v1/listings` body strict `{purpose, category, subtype}` (unknown fields refused as `validation_error` `unknown_field`) returns `201` with the listing and `Location`-style id in the body; signed in, CSRF enforced by the existing authentication class; no-store. `GET /api/v1/listings/{id}` (owner only, `404` otherwise, no-store) and `GET /api/v1/me/listings` (own listings, optional `status` filter limited to known statuses, cursor pagination with the project's `StandardCursorPagination` and the same bad-cursor handling as notifications, newest first by a unique ordering field, `no-store`). A Suspended owner may list and read (`view_drafts`) but not create; a Deactivated account has no session. Routes are signed in (`RequireSignedIn`); the type and capability checks live in the services.
- **Seeds and policies**: the `open_drafts` cap policy already exists (Story 1.7 seed); do not change it; add tests that the cap value 20 is what the service reads (not a constant).
- **Docs**: new `docs/listings.md` (the model, the registry, the create rules, the wire shape, how later stories add fields and bump the registry version and lock, ownership and the not-found rule) and `docs/registry.md` (file format, flags, the private-class rule, versioning, the lock script); updates to `docs/account-states.md` (capabilities no longer pending), `docs/rate-limits.md` (open Drafts cap in use), `docs/authorisation-matrix.md` (regenerated) and `README.md`; `openapi.yaml` regenerated. Add the Listing resource to the authorisation matrix generator input if it enumerates resources. All earlier tests, convention tests and import-linter contracts keep passing (add `listings` to any contract lists that need it; keep the layering: `listings` imports `identity.selectors`/`policy`, `catalog.selectors`/registry and `kernel`, never their models).

**Never:** let a Visitor, an administrator, a Suspended or Deactivated account create a Draft; store or accept `owner_account_id` from a client; return another account's listing or confirm its existence (404, never 403); hand-copy taxonomy or enum values into serializers (generate them from the registry); import `identity.models` or `catalog.models` from `listings`; create a Listing outside `listings.services`; put free text, addresses or any private data in the audit metadata; add Claude attribution to commits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Create, individual | Active individual, `sale`, `house`, `house` | 201 listing: status draft, visibility private, revision 1, registry_version 1, fields set, media and amenities empty; revision row 1; audit `listing.draft_created` | N/A |
| Create, agency | Active agency | Draft owned by the agency account; every session of that account can read it | N/A |
| Create, email-unverified | email_unverified owner | allowed (FR-74) | N/A |
| Create, suspended | suspended owner | refused per the matrix (`forbidden`); nothing created | envelope |
| Create, deactivated | deactivated owner (no session) | unauthenticated | envelope |
| Create, administrator | admin session | refused (`forbidden`); never an owner | envelope |
| Create, Visitor | no session | 401 `unauthenticated` | envelope |
| No CSRF | POST without a token | 403 `forbidden` | envelope |
| Subtype mismatch | `apartment` with subtype `shop` | 400 `subtype_not_in_category` on `subtype`; nothing created | envelope |
| Plot File | `plot` + `plot_file` | allowed; distinct from `residential_plot` | N/A |
| Missing or invalid values | blank, wrong type, unknown choice | stable codes at once (`required`, `invalid`, `invalid_choice`), values not echoed | envelope |
| Unknown field | `owner_account_id`, `status`, `revision`, anything else | 400 `unknown_field`; nothing created | envelope |
| Open-draft cap | 20 open drafts, then another | 429 throttled; counts only status draft; other owners unaffected | envelope |
| Cap race | two parallel creates at 19 drafts | exactly one 201 and one 429 | threaded test |
| Cap reads the policy | change the seeded `open_drafts` limit | the service follows it | test |
| Read own | owner GET `/listings/{id}` | 200 listing | N/A |
| Read other's, unknown, malformed id, Visitor, administrator | any | 404 `not_found` (Visitor: 401 on the signed-in route) | envelope |
| Own list | owner with 3 drafts | newest first, cursor pagination, status filter, no other owner's rows | test |
| Bad cursor, bad status filter | garbage | 400 `validation_error` envelope | envelope |
| Response shape | any listing response | exactly the wire shape, no `owner_account_id` | contract test |
| Registry served | `GET /api/v1/registry` | version, taxonomy, fields; `ETag`; 304 on match; public cache header | test |
| Registry loader rules | bad YAML fixtures | each rule raises `ImproperlyConfigured` (private+searchable/sortable/valuation_input, duplicate id, subtype in two categories, enum mismatch, missing version, unknown key) | test |
| Registry lock | edit YAML without lock | test fails with the bump message | test |
| Capabilities | `edit_drafts`, `view_drafts` | no longer pending; matrix rows tested for all four states and the admin | test |
| Audit | create | exactly one `listing.draft_created`, metadata only enumerated values | test |
| Rollback | failure after insert | no listing, no revision, no audit | test |
| Privacy | logs, audit, errors | no owner id leaks into responses; canary over responses | test |
| Contract | `openapi.yaml` | routes, enums from the registry, listing schema, freshness passes | test |

</frozen-after-approval>

## Code Map

- `apps/catalog/registry/fields.yaml`, `fields.lock`, `apps/catalog/registry.py`, `apps/catalog/apps.py` (load at start-up), `scripts/registry_lock.py`
- `apps/listings/models.py`, `migrations/0001_initial.py`, `services.py`, `selectors.py`, `policy.py` if needed, `events.py` only if used, `apps/listings/apps.py` (register audit action)
- `apps/identity/capabilities.py` (pending set), `apps/presentation/api/registry.py`, `apps/presentation/api/listings.py`, `config/urls.py`, `config/settings/base.py` (install `apps.listings` if not yet installed)
- `openapi.yaml`, `docs/authorisation-matrix.md` regenerated
- `tests/catalog/test_registry.py`, `tests/listings/test_create_draft.py`, `tests/api/test_listings_api.py`, `tests/api/test_registry_api.py`
- `docs/listings.md`, `docs/registry.md`, `docs/account-states.md`, `docs/rate-limits.md`, `README.md`

## Tasks & Acceptance

**Execution:**
- [ ] registry file, loader, lock, script, public endpoint
- [ ] listings app: models, migration, create service, selectors, audit
- [ ] capabilities no longer pending; the open-Draft cap
- [ ] listing endpoints and the wire-shape contract test
- [ ] tests for every matrix row; docs and regenerated contracts

**Acceptance Criteria:**
- Given I am signed in with an Active or Email-unverified account, when I choose sale or rent, a Category and a Subtype, then a Draft is created owned by my account (an Agency's belongs to the Agency) in status draft with revision 1 and the current registry version, and a Subtype outside the Category is rejected with a field-level error, with Plot File a distinct Subtype of Plot.
- Given the registry, when the backend starts, then the Field registry YAML is loaded and served at `GET /api/v1/registry`, the loader rejects a private-class field flagged searchable, sortable or valuation_input, and the registry contains only the fields introduced so far.
- Given a Suspended or Deactivated account, or an Administrator, when it tries to create a Draft, then it is refused according to the FR-74 matrix and administrators cannot own Listings.
- Given I already have 20 open Drafts, when I start another, then the 429 envelope is returned.
- Given a Listing resource, when it is returned by the API, then it has the shape `{id, status, visibility, revision, registry_version, fields, media, amenities}` with fields keyed by flat registry ids, asserted by a contract test.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1, backend half (blind, edge-case, verification-gap).

| # | Finding | Verdict | Route |
|---|---------|---------|-------|
| 1 | The eligibility re-check after `lock_account` reused the stale actor, so an account suspended or deactivated between the first check and the lock could still create a Draft (edge) | high | patch: decided from the locked account snapshot, with tests that change the state between the two checks |
| 2 | Crafted cursors (non-string fields, extreme datetimes, status mismatch) and huge `page_size` or non-canonical ids could 500 or alias a listing (edge, blind) | high | patch: every malformed shape is `InvalidCursor` (400), the status filter is bound into the cursor, canonical ids only, 1 to 3 digit page sizes |
| 3 | Registry ids up to 48 characters could overflow the 32-character columns; loader accepted unhashable keys, merge keys and contradictory applicability (edge, blind) | medium | patch: loader limits and rules, tests per rule |
| 4 | The version lock was advisory and the script allowed a lower version (blind) | medium | patch: script refuses a lower version; documented that reviewers must read `fields.yaml` and `fields.lock` together |
| 5 | ETag ignored serializer changes (blind) | low | patch: response-shape version mixed into the tag |
| 6 | The database CHECK constraints had no direct tests; `subtype_not_in_category` showed the generic message; deactivated and cursor tests were loose; the cap race test was timing-based (verification-gap) | high | patch: constraint tests, distinct message, exact assertions, deterministic lock-ordering test and repeated rounds |
| 7 | Taxonomy frozen into database constraints means a taxonomy change needs a migration (blind, edge) | medium | accept, documented, with a drift test (matches the spec; taxonomy changes rarely) |
| 8 | Private-class fields are listed in the public registry response; `ai_extractable` and `quota_identity` allowed on private fields (blind) | low | accept, documented (metadata, never values) |
| 9 | Own-list returns 403 for administrators while the detail route returns 404; hand-rolled cursor instead of the shared pagination class (blind, edge) | low | accept, documented: presentation cannot hold listing models, so listings does keyset paging |
| 10 | Index shape for the unfiltered own list (blind) | low | patch: two composite indexes |

Deviations recorded: cap counted after `lock_account` with `DomainError("throttled")` and no `Retry-After` (kernel.ratelimit has no cap counter); cursor datetimes must fall between years 2000 and 9000; PyYAML moved from dev to runtime dependencies.

## Design Notes

Decisions made without asking: the registry is a plain YAML file loaded and validated at start-up with a lock file that forces a version bump whenever it changes, so `registry_version` stored on every Listing is trustworthy; Category and Subtype are code-defined taxonomy inside the registry file as the PRD addendum says; the three identity fields are stored as columns with CHECK constraints but exposed as flat registry ids (AD-24); owner identity is a plain UUID column so `listings` never imports identity models; unknown, foreign and administrator reads are all `404` so existence is never confirmed; the Draft list is `GET /api/v1/me/listings` so the public discovery routes can own `/api/v1/listings` collection semantics later; the 20-Draft cap is counted inside the lock-account transaction so parallel creates cannot overshoot.

## Verification

**Commands (backend):**
- `uv run python manage.py migrate --database=migrator`
- `env -u DJANGO_SETTINGS_MODULE uv run lint-imports; uv run ruff check . && uv run ruff format --check . && env -u DJANGO_SETTINGS_MODULE uv run pytest -q && uv run python manage.py makemigrations --check --dry-run`
- `uv run python manage.py spectacular --validate --file openapi.yaml && uv run python scripts/authz_matrix.py` -- regenerate and commit
