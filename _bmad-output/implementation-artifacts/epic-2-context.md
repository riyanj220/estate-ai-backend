# Epic 2 Context: Create and Publish Listings

<!-- Compiled by hand from epics.md, the PRD addendum and ARCHITECTURE-SPINE.md. Edit freely. -->

## Goal

Individual and Agency owners create Drafts manually, with images, validate and publish them within the 2 or 10 monthly quota, and manage the Listing lifecycle; administrators configure quotas and suspend accounts. The epic also completes profile pictures and Agency logos from Epic 1.

## Stories (see `epics.md` for acceptance criteria)

Sprint 6 (Draft building A): 2.1 Start a Draft with purpose, Category and Subtype; 2.2 Conflict-safe editing with title, description and price; 2.3 Area and measurement units; 2.4 House and Apartment details; 2.5 Plot and Commercial details; 2.6 Sale and rent terms. Sprint 7: 2.7 Public location and Private address; 2.8 Amenities and seller-declared nearby facilities; 2.9 Image upload pipeline and storage; 2.10 Media endpoint, cover image and ordering; 2.11 Profile pictures and Agency logos. Later: 2.12 Visibility policy and public projections; 2.13 Draft preview and management; 2.14 Publication quota policy; 2.15 Validate and publish; 2.16 Sample data; 2.17 Lifecycle; 2.18 Republication; 2.19 Correction window; 2.20 Administrator quota policy; 2.21 Administrator suspension and reactivation.

## Architecture decisions that bind every Epic 2 story

- **One canonical Listing and one Field registry (AD-4).** The only property model is `PropertyListing` plus location, private address, residential, plot and commercial details, terms, media, amenities and `ListingRevision`, all owned by `listings`. The Field registry is YAML in `catalog` (versioned, loaded at start-up, served at `GET /api/v1/registry`); it defines per field: id, type, requirement level, applicability, allowed values, class (public or private) and the flags `searchable`, `sortable`, `ai_extractable`, `valuation_input`, `quota_identity`. The loader rejects a private-class field flagged searchable, sortable or valuation_input. Validation, serializers, forms, AI schemas, filter schema and OpenAPI enums are generated from the registry. Category and Subtype (and purposes, lifecycle states, area units) are code-defined; cities, localities, amenities and facilities are database-managed reference data and are never OpenAPI enums.
- **Wire shape (AD-24).** A Listing resource is `{id, status, visibility, revision, registry_version, fields:{<registry_id>:value}, media, amenities}`; fields are flat registry ids whichever table stores them; a mutation is `{base_revision, fields:{...}}`; unknown is `null`; money is a string; areas carry a unit.
- **Services take `actor` first (AD-22); only the owning app writes (AD-3).** `listings` may use `identity.selectors`/`identity.policy` and `catalog.selectors`, never their models; no foreign key into another app's models (plain UUID columns); presentation builds public responses from projection types, never model instances.
- **Revision-checked edits (FR-22, AD-10).** Every Listing change carries `base_revision`; a stale revision is `409` with the current data; provenance (`origin` manual or ai, `confirmed`) is set by the service from the actor and call path, never from a client payload.
- **Single visibility policy (AD-5)**: `listings.selectors.public_listings()` and `listings.policy.can_view(viewer, listing)` are the only implementations (Story 2.12).
- **Publication and quota (AD-7)**: only `listings.publication` sets status to published, in one transaction (lock account, then lock listing), with `PublicationEvent` and idempotency (Stories 2.14, 2.15).
- **Shared privacy patterns (AD-9)**: Listing text goes through `kernel.privacy.patterns` (contact details rejected, prices/areas/counts not).
- **Audit in the same transaction; side effects after commit (AD-18); notifications after commit via `kernel.notifications.notify` with schema-registered kinds.**
- **Abuse limits (NFR-10)**: 20 open Drafts per account (seeded policy `open_drafts`, a cap).

## Account rules (FR-74 matrix, `docs/account-states.md`)

Capabilities `view_drafts` and `edit_drafts` are allowed for Active and Email-unverified accounts (edit denied when Suspended; both denied when Deactivated); `publish` only for Active; administrators are never eligible Listing owners and are refused. They are `PENDING_CAPABILITIES` until the story that owns the feature removes them.

## Process (from the Epic 1 retrospective)

Use `docs/story-checklist.md` (account-existence side channels, after-commit effects, emailed-token pages, test rules) in every spec and reviewer brief; low-entropy fake-word test values only; full Playwright run takes about 16 minutes (background it); `docs/planning/epic-1-accepted-deviations.md` lists accepted Epic 1 deviations.
