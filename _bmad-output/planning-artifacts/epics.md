---
status: final
stepsCompleted: [1, 2, 3]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-estate-ai-backend-2026-11-18/prd.md
  - _bmad-output/planning-artifacts/prds/prd-estate-ai-backend-2026-11-18/addendum.md
  - _bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md
---

# EstateAI - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for EstateAI, decomposing the requirements from the PRD, UX Design if it exists, and Architecture requirements into implementable stories.

## Requirements Inventory

### Functional Requirements

FR-1: Individual registration [P1, BE+FE]. A Visitor can register an Individual account with full name, email address, password and phone number, all mandatory.
FR-2: Agency registration [P1, BE+FE]. A Visitor can register an Agency account through a dedicated business form collecting agency name, business email, business contact number, office address and city (all mandatory) and sign-in credentials.
FR-3: Sign-in and sign-out [P1, BE+FE]. An account holder can sign in with registered credentials and sign out securely.
FR-4: Email verification [P1, BE+FE]. An account holder can verify their email address from a link sent to it.
FR-5: Password reset and change [P1, BE+FE]. An account holder can reset a forgotten password by email and change it while signed in.
FR-6: Individual profile [P1, BE+FE]. An Individual account holder can view and update profile information (name, phone) and upload or change a profile picture.
FR-7: Agency business profile [P1, BE+FE]. An Agency account holder can view and update its business profile: name, contact details, address, description and logo.
FR-8: Account deactivation or deletion request [P7, BE+FE]. An account holder can request deactivation or deletion of their account.
FR-9: Administrator accounts and protected admin area [P1, BE+FE]. Administrator accounts are provisioned by the platform; there is no public Administrator registration. Administrators sign in to a protected admin area and can reset or change their password.
FR-10: Strengthened Administrator authentication [P7, BE+FE]. Administrator sign-in supports a second factor (time-based one-time code), in addition to the baseline controls in FR-9.
FR-74: Account states and capabilities [P1, BE+FE]. Every Individual and Agency account is in exactly one **Account state**, which determines what it can do, how its sessions behave and whether its Listings are discoverable. **[B19]**
FR-76: Notifications and email delivery [P1, BE+FE]. The system tells users about events that affect them through in-app notifications and transactional email, and the local environment captures email so every flow can be tested without an external mail service.
FR-11: Create a Draft with purpose, Category and Subtype [P2, BE+FE]. An Individual or Agency account can create a Draft, choosing sale or rent, a Category (House, Apartment, Plot, Commercial) and a supported Subtype.
FR-12: Category-appropriate Listing details [P2, BE+FE]. The creator can enter title, description, city and locality, area with unit, Asking price and the Category-specific attributes, and the form shows only fields relevant to the selected Category and Subtype.
FR-13: Public location and Private address [P2, BE+FE]. The creator can set city and locality from the supported reference list and may add an exact Private address.
FR-14: Amenities and Seller-declared nearby facilities [P2, BE+FE]. The creator can select Amenities and Seller-declared nearby facilities (mosque, park, gym, school, hospital).
FR-15: Listing images [P2, BE+FE]. The creator can upload multiple images, choose a cover image and order them.
FR-16: Draft management and preview [P2, BE+FE]. The creator can save an incomplete Draft, reopen and edit it, preview it as it will appear publicly and delete an unpublished Draft.
FR-17: Validation and immediate Publication [P2, BE+FE]. The owner can publish a Draft; the system validates all mandatory information and the Publication quota and, if both pass, publishes immediately.
FR-18: Publication quota [P2, BE+FE]. The system enforces a monthly Publication quota per account type, seeded at **2 for Individual accounts and 10 for Agency accounts**, and shows owners their allowance and remaining Publications.
FR-19: Listing lifecycle and dashboard [P2, BE+FE]. An owner can see all their Listings by Listing status, edit them, archive them and mark them sold or rented.
FR-20: Republication [P2, BE+FE]. An owner can republish an eligible archived Listing, subject to revalidation and Publication quota.
FR-21: Ownership and authorisation [P1, BE]. The system prevents an account from viewing or changing Listings, Drafts, Assistant sessions, Conversations, AI Price Estimates, Reports, support requests, Verification requests and evidence, favourites or uploaded media that it does not own, participate in or is not authorised to see.
FR-22: Edit conflict protection [P2, BE]. When a manual edit and an AI update (or two sessions) change the same Draft or Listing, the system never silently overwrites newer accepted data.
FR-23: Contact details kept out of Listing text [P2, BE]. The system prevents seller and agency phone numbers from appearing on Listings.
FR-75: Listing visibility and owner actions [P2, BE+FE]. Who can see a Listing, and what its owner and an Administrator can do, are fully determined by the Listing status, the Visibility status and the owner's Account state. **[B18]**
FR-24: Start and choose AI-assisted creation [P5, BE+FE]. An account can choose AI-assisted creation instead of the manual form and start an Assistant session in ordinary English.
FR-25: Extraction and follow-up questions [P5, BE+FE]. The AI Listing Assistant identifies Listing attributes from the user's messages, populates the Draft with validated values and asks follow-up questions for missing information, required fields first.
FR-26: Seller-declared nearby facilities in conversation [P5, BE+FE]. The AI Listing Assistant extracts Seller-declared nearby facilities and Amenities from natural language and asks about them when relevant.
FR-27: Context retention and corrections [P5, BE+FE]. The assistant preserves details already supplied and lets the user correct them.
FR-28: Grounded real-estate knowledge (RAG) [P5, BE]. The assistant draws on a curated real-estate knowledge base (Pakistani housing types, common Amenities, property measures and terminology such as marla, possession and balloting) to guide its questions and explanations when helpful.
FR-29: Description generation, review, switching and resume [P5, BE+FE]. The assistant generates a professional description from confirmed details; the user can review and manually edit anything, switch between AI and manual on the same Draft, and save and resume an unfinished session.
FR-30: Integrity and explicit approval [P5, BE+FE]. AI-extracted information passes the same validation as manually entered information, the assistant never invents missing facts, and an AI-assisted Listing requires explicit user approval before Publication.
FR-31: Graceful degradation [P5, BE+FE]. If the AI provider or knowledge retrieval is unavailable, slow or rate-limited, the user is told and can continue manually without losing the Draft.
FR-32: AI usage controls and data protection [P5, BE]. The system limits AI use per account and per visitor and does not send private data to the AI provider.
FR-78: Real-estate knowledge base [P5, BE]. The project provides the curated real-estate knowledge base that FR-28 depends on.
FR-33: Public browsing [P3, BE+FE]. Visitors and signed-in accounts can browse Published listings and view full Listing details without signing in.
FR-34: Conventional filters and sorting [P3, BE+FE]. Users can filter by city, locality, Category, Subtype, listing purpose, price range, area range, bedrooms, bathrooms, parking, Amenities and Seller-declared nearby facilities (combinable, driven by the Field registry) and sort by price and listing date; results are paginated.
FR-35: Natural-language text search [P5, BE+FE]. Users can search with free text and receive matching Published listings.
FR-36: Interpretation transparency and unknown data [P5, BE+FE]. The system shows the Search interpretation, lets the user edit it, distinguishes required criteria from preferences and handles missing data explicitly.
FR-37: Voice search [P5, BE+FE]. Users can speak a query; it is transcribed to text, shown for correction and then handled as in FR-35.
FR-38: Search availability and abuse control [P5, BE+FE]. Natural-language and voice search remain usable when AI capacity is limited.
FR-39: Listing detail with provenance [P3, BE+FE]. Listing details distinguish Individual-owned from Agency-owned Listings, show Agency name and branding and, where granted, Verification status.
FR-40: Favourites [P3, BE+FE]. An Individual or Agency account can save Published listings to a favourites list, view it and remove items.
FR-41: Agency public profile and Agency Listings [P3, BE+FE]. Users can view an Agency's public profile and browse its Published listings.
FR-42: Request an AI Price Estimate [P4, BE]. An authenticated account can request an AI Price Estimate for property attributes it enters or for its own Draft. For a Published listing, users see the stored estimate (FR-44).
FR-43: Baseline during Listing creation [P4, BE+FE]. During creation, the creator can view the AI Price Estimate as a recommended baseline price without it being required.
FR-44: Compare Asking price with the Estimated Property Value [P4, BE+FE]. Users can see the Estimated Property Value next to the Asking price and whether the Asking price is above or below it.
FR-45: 5-Year Price Forecast [P4, BE+FE]. Users can view a 5-Year Price Forecast for a supported Listing where available.
FR-46: Unavailable and uncertain results [P4, BE+FE]. When data is insufficient or the Listing is not supported, the system says so instead of showing a number.
FR-47: Concise limitation note and documented methodology [P4, FE]. Wherever an Estimated Property Value or 5-Year Price Forecast appears, the product gives one concise, unobtrusive explanation of its limitations, and the methodology is documented.
FR-77: Synthetic dataset and valuation model [P4, BE]. The project provides the synthetic Karachi dataset, its generator and the trained, versioned valuation model that FR-42 to FR-47 depend on.
FR-48: Start an Inquiry [P6, BE+FE]. An Individual or Agency account can start a Conversation with the owner of a Published listing.
FR-49: Real-time text messages [P6, BE+FE]. Participants can send text Messages and receive them in real time.
FR-50: Inbox, history and unread [P6, BE+FE]. Participants can view all their Conversations, full history and which Conversations have unread Messages.
FR-51: Conversation privacy [P6, BE]. Only the two participants can read or send Messages in a Conversation.
FR-52: Contact privacy and internal-only communication [P6, BE]. Seller and Agency phone numbers are never exposed in Listings or Conversations by the system, and buyer–seller communication happens through EstateAI.
FR-53: Conversation continuity and restrictions [P6, BE]. Conversations are kept when a Listing is archived, sold or rented, and suspended accounts cannot send Messages.
FR-54: Report a Conversation or Message [P7, BE+FE]. A participant can report abusive or inappropriate interactions; a Conversation Report requires the reporter to select the Message(s) at issue.
FR-55: Agency dashboard [P7, BE+FE]. The Agency account has a dashboard showing total Listings, Listings by Listing status, the Publication quota and remaining allowance, and Inquiries received (overall and per Listing), and can filter its Listings by status, Category, location and date.
FR-56: Listing performance statistics [P7, BE+FE]. The Agency can see Listing performance statistics: a view count per Listing.
FR-57: Agency Verification request [P7, BE+FE]. An Agency can operate without Verification, may submit a Verification request with business information, see its status and outcome and resubmit after rejection.
FR-58: Verified status display [P7, BE+FE]. Only an Administrator can grant or revoke Verification; verified status appears on the Agency's public profile and Listings.
FR-59: Account suspension and restrictions [P2, BE+FE]. An Administrator can suspend or reactivate Individual and Agency accounts and apply supported restrictions.
FR-60: Quota policy management [P2, BE+FE]. An Administrator can view and change the monthly Publication quota separately for Individual and Agency accounts without code changes, and view usage by account.
FR-61: Account directory and review [P7, BE+FE]. An Administrator can view, search and filter Individual and Agency accounts, see status and profile details, review an account's Listings and review account-related Reports and deactivation or deletion requests.
FR-62: Agency Verification review [P7, BE+FE]. An Administrator can review pending requests and submitted information, approve, reject (with a recorded reason) or revoke Verification, and see decision history.
FR-63: Listing moderation [P7, BE+FE]. An Administrator can view all non-Draft Listings (Drafts only with a recorded reason, FR-9), filter by owner, Category, Listing status and date, review information and images, remove violating Listings, temporarily restrict serious-risk Listings, restore Listings and record reasons.
FR-64: Report a Listing [P7, BE+FE]. An Individual or Agency account can report a suspicious, misleading or inappropriate Listing, including reporting AI-generated content within it.
FR-65: Report review [P7, BE+FE]. An Administrator can view Reports with their reason and evidence, mark them resolved or dismissed and see unresolved counts.
FR-66: Suspicious and duplicate Listing flags [P7, BE+FE]. Administrators can review Listings flagged as suspicious or duplicate.
FR-67: Reference data management [P7, BE+FE]. An Administrator can manage the Amenity and Seller-declared nearby facility catalogue and curated city and locality data; the system prevents unsafe deletion of reference data in use.
FR-68: AI and ML oversight [P7, BE+FE]. Administrators can see AI feature status, aggregated AI request failures, deployed valuation model version information and failed valuation requests without exposing private data, and can disable a malfunctioning AI feature.
FR-69: Audit trail [P7, BE+FE]. Security-sensitive and policy actions are recorded and Administrators can review them.
FR-70: Administrator dashboard [P7, BE+FE]. The dashboard shows total Individual accounts, total Agency accounts, total Published listings, Listings by Listing status, pending Verification requests, unresolved Reports and relevant platform activity.
FR-71: Support requests [P7, BE+FE]. An Individual or Agency account can submit a platform support request, and Administrators can see and close it.
FR-72: Sample marketplace data [P8, BE+FE]. The system can be loaded with a repeatable sample Karachi marketplace: Individual and Agency accounts, Listings across all four Categories with images, localities, Amenities, Seller-declared nearby facilities and valuation data for the Categories and localities the model supports (FR-77).
FR-73: Local operation [P8, BE+FE]. The full product (frontend and backend) can be set up, run, tested and presented on a development machine without deployment.
FR-79: Evaluation assets and harness [P8, BE]. The project provides labelled evaluation sets and a repeatable harness for the success metrics.
FR-80: Technical documentation [P8, BE+FE]. The project provides technical documentation covering architecture, the API contract, synthetic data, the model, evaluation and limitations.

### NonFunctional Requirements

NFR-1: Performance. Conventional search p95 < 500 ms server time; Listing detail p95 < 300 ms; AI Price Estimate p95 < 1 s; Message delivery to an online recipient p95 < 1 s (SM-7); natural-language or voice search p90 ≤ 10 s end to end with a visible progress indicator; AI Listing Assistant turn p90 ≤ 15 s.
NFR-2: Security. Authentication and authorisation enforced server-side; protected against common web attacks (cross-site scripting, request forgery, injection) and checked against an OWASP ASVS Level 1 subset `[ASSUMPTION]`; uploads validated (FR-15); abuse limits per NFR-10; secrets kept out of source control.
NFR-3: Privacy. Phone, email and Private address minimised everywhere, kept out of AI provider requests by redaction (FR-32, residual risk disclosed), never in public responses or machine-learning inputs; verified by SM-6.
NFR-4: Reliability. AI or retrieval failure degrades to manual and conventional paths (FR-31); publication and quota accounting are atomic and idempotent (FR-18); no state change is half-applied, verified by fault-injection tests (for example interrupting a Publication mid-transaction).
NFR-5: Data integrity. One canonical Listing data model serves creation, search, AI assistance and valuation, with a single definition of field meanings (SYS-033; verified by the validation and authorisation tests of FR-21 and FR-22); unknown is never recorded as zero or false.
NFR-6: Accessibility and usability. Responsive web (desktop and mobile browsers); search, Listing creation and chat usable by keyboard and meeting WCAG 2.1 AA for those flows `[ASSUMPTION]`; clear validation messages; English only.
NFR-7: Maintainability and testability. Automated tests for domain rules, authorisation matrix, concurrency and AI contract validation; one documented contract between frontend and backend; reproducible local setup.
NFR-8: Observability. Operational logging and AI failure counts; a log scan (as in SM-6) finds no seeded canary private values.
NFR-9: Compatibility. Current versions of Chrome, Edge and Firefox on desktop and Android, and Safari on iOS on a best-effort basis; voice search depends on browser support and degrades to text.
NFR-10: Abuse limits. All configurable, defaults `[ASSUMPTION]`: registration 10 per hour per client; verification and reset emails 5 per hour per account; Messages 60 per minute per account; new Conversations 20 per day per account; favourites 500 per account; Reports 10 per day and support requests 5 per day per account; 20 open Drafts per account; Assistant messages 30 per hour per account; natural-language and voice searches 10 per minute per Visitor and 30 per hour per signed-in account, with at least half of AI capacity reserved for signed-in Listing creation; estimate requests per FR-42; sign-in per FR-3. A "client" is identified by network address (behind a trusted proxy, using forwarded headers) and a session cookie, on a best-effort basis, so shared addresses can be throttled together. Extra accounts can be used to evade Publication quotas; this is accepted for the prototype and limited by email verification and these limits **[B23]**.

### Additional Requirements

- **No starter template.** Architecture specifies none; the backend (Django 5.2 LTS project with modular apps) and frontend (Next.js 16) are scaffolded by hand, so Epic 1 includes scaffolding stories for both repositories, local Docker Compose, CI gates and the OpenAPI sync.
- **Two repositories.** Backend (this repo) and a separate Next.js frontend repo; contract is the committed `openapi.yaml` (+ `events.schema.json`); stories are tagged [BE], [FE] or [BE+FE] per the PRD.
- **Architecture decisions to be honoured by every story (AD-1 to AD-26):**
  - AD-1 Paradigm and app map [ADOPTED]: one Django app per domain in the table above.
  - AD-2 Dependency direction: an app imports only public modules of lower layers.
  - AD-3 Data ownership and access [ADOPTED]: only the owning app writes its tables, through its services (see the ownership table in Structural Seed).
  - AD-4 One canonical Listing and one Field registry: `PropertyListing` plus `PropertyLocation`, `PrivatePropertyAddress`, `ResidentialDetail` / `PlotDetail` / `CommercialDetail`, `ListingTerms`, `PropertyMedia`, `ListingAmenity` and `ListingRevision` is the only property model.
  - AD-5 Single visibility policy: `listings.selectors.public_listings()` and `listings.policy.can_view(viewer, listing)` are the only implementations;
  - AD-6 Account state, capabilities and private reads: `identity` owns account state as separate conditions (email verified, suspended, deactivated) and exposes `can(actor, capability)` (FR-74 matrix) and `lock_account(account_id)`.
  - AD-7 Publication and quota transaction: only `listings.publication` (`publish`, `republish`, `restore_after_moderation`) sets status to published;
  - AD-8 AI gateway and redaction [ADOPTED]: `kernel.ai` exposes ports (LLM, embeddings, speech-to-text) that accept only `RedactedText`;
  - AD-9 Shared privacy patterns: `kernel.privacy.patterns` is the only definition of phone, email and address patterns, with a false-positive test set (prices, areas, counts).
  - AD-10 AI proposes, services dispose: the Assistant returns candidate field updates (schema from the registry) and applies them only through `listings.services.apply_updates(actor, listing_id, base_revision, updates)`, which validates against the registry and rejects a stale revision (409).
  - AD-11 One filter schema for conventional and natural-language search: `ListingFilter` and its registry-driven translator live in `catalog.filters`.
  - AD-12 Valuation: inference runs in-process in `valuation` on the AD-14 executor (target p95 under 1 s).
  - AD-13 Realtime messaging [ADOPTED]: Messages are sent by REST (the `Idempotency-Key` is the message's `client_message_id`);
  - AD-14 Runtime and concurrency [ADOPTED]: Daphne runs one process with one worker; the in-memory channel layer is used only in this shape and any second worker is forbidden.
  - AD-15 Media [ADOPTED, revised]: all media is served by one Django endpoint that applies AD-5 and AD-22 at request time;
  - AD-16 API contract: the backend generates OpenAPI (drf-spectacular) under `/api/v1/` and commits `openapi.yaml`;
  - AD-17 Auth, session and transport: Django sessions in an HttpOnly, Secure, SameSite=Lax cookie with CSRF;
  - AD-18 Side effects and events: audit records are written in the same transaction as the action.
  - AD-19 Frontend structure [ADOPTED]: Next.js App Router. Public pages are server components fetching from Django with the cookie forwarded;
  - AD-20 Environments, configuration and supply chain: settings split into base, local, test and prod, driven by environment variables;
  - AD-21 Test gates: CI runs the import-linter contract; an AST check banning provider and network libraries outside `kernel.ai`;
  - AD-22 Actor and authorisation in services: every public mutating service and every non-public selector takes `actor: Actor` (kind user, admin or system;
  - AD-23 Cross-app consequences, erasure and retention: a state change whose consequences span apps (deactivation, anonymisation, approved deletion, suspension) is one named use case in `governance`, run in one transaction that calls each owner's service in layer order, each owner writing its audit record.
  - AD-24 Wire shape: a Listing resource is `{id, status, visibility, revision, registry_version, fields:{<registry_id>:value}, media, amenities, ...}`;
  - AD-25 Abuse limits and AI budget: `kernel.ratelimit` holds one policy table covering every NFR-10 limit (scope, window, limit), with counters persisted in PostgreSQL (no Redis).
  - AD-26 Private-data containment: phone, email and Private address exist only in private-class fields and are never in public projection types, search, sort, ML inputs, AI payloads, URLs, logs or error bodies.
- **Stack (pinned by lockfile at scaffold):** Python 3.12, Django 5.2 LTS, DRF 3.18.x, drf-spectacular 0.30.x, Channels 4.3.x with Daphne, PostgreSQL 18 + pgvector 0.8.x (image pgvector/pgvector:0.8.7-pg18-trixie), psycopg 3.3.x, import-linter 2.x, fastembed 0.9.x, Node 24 LTS or 26, Next.js 16.x.
- **To verify at scaffold:** ASGI sync-view thread model (chat latency under AI load), `ListingTerms` and `Locality.kind` shapes, Daphne and Channels pairing, import-linter tier-4 peer independence, free-tier provider limits and data-retention terms.
- **Operational envelope:** local-first (Docker Compose for PostgreSQL and dev mail catcher on 127.0.0.1; host processes for Daphne and Next dev); deployment, production channel layer, storage and email providers are deferred.
- **Phase order (PRD §6.3):** P1 identity and foundation; P2 listings and publication; P3 discovery; P4 valuation; P5 AI capabilities; P6 messaging; P7 agency operations and governance; P8 integration, sample data and evaluation. After P2, P3, P4 and P6 are independent.
- **Two-repository execution rule:** a story tagged [BE+FE] is executed backend first (its backend half merges and regenerates the committed `openapi.yaml`), then frontend (which regenerates the typed client and consumes it); heavy stories may be run as two sessions. [BE] stories touch only this repository and [FE] stories only the frontend repository.
- **UX gate:** no UX design contract exists. Run `bmad-ux` before the first user-facing frontend story (Story 1.15); frontend stories carry behaviour from the PRD journeys and leave visual design to that contract.
- **Decisions needed before they block a story:** the initial Karachi locality seed list (PRD open question OQ-2, needed by Story 2.7); the nearby-facility catalogue and unmatched-mention handling (OQ-1, Story 2.8 and Story 5.11); the Agency Verification evidence (OQ-3, Story 8.3).
- **Kernel primitives are built before first use:** event bus and idempotency (Story 1.8), runtime executor, flags, AI request log and retention hooks (Story 1.9), audit and reasoned private reads (Story 1.10), mail port (Story 1.13), shared contact patterns (Story 1.14), rate-limit policy (Story 1.7); later stories depend on them by number.

### UX Design Requirements

No UX design contract exists (no DESIGN.md or EXPERIENCE.md). Decision: proceed without UX now and run `bmad-ux` before building the frontend; frontend stories carry UI behaviour from the PRD journeys (UJ-1 to UJ-6) and flag where a design decision is needed. No UX-DR items are extracted.

### FR Coverage Map

FR-1: Epic 1 - Individual registration
FR-2: Epic 1 - Agency registration
FR-3: Epic 1 - Sign-in and sign-out
FR-4: Epic 1 - Email verification
FR-5: Epic 1 - Password reset and change
FR-6: Epic 1 - Individual profile
FR-7: Epic 1 - Agency business profile
FR-8: Epic 10 - Account deactivation or deletion request
FR-9: Epic 1 - Administrator accounts and protected admin area
FR-10: Epic 10 - Strengthened Administrator authentication
FR-11: Epic 2 - Create a Draft with purpose, Category and Subtype
FR-12: Epic 2 - Category-appropriate Listing details
FR-13: Epic 2 - Public location and Private address
FR-14: Epic 2 - Amenities and Seller-declared nearby facilities
FR-15: Epic 2 - Listing images
FR-16: Epic 2 - Draft management and preview
FR-17: Epic 2 - Validation and immediate Publication
FR-18: Epic 2 - Publication quota
FR-19: Epic 2 - Listing lifecycle and dashboard
FR-20: Epic 2 - Republication
FR-21: Epic 1 - Ownership and authorisation
FR-22: Epic 2 - Edit conflict protection
FR-23: Epic 2 - Contact details kept out of Listing text
FR-24: Epic 5 - Start and choose AI-assisted creation
FR-25: Epic 5 - Extraction and follow-up questions
FR-26: Epic 5 - Seller-declared nearby facilities in conversation
FR-27: Epic 5 - Context retention and corrections
FR-28: Epic 5 - Grounded real-estate knowledge (RAG)
FR-29: Epic 5 - Description generation, review, switching and resume
FR-30: Epic 5 - Integrity and explicit approval
FR-31: Epic 5 - Graceful degradation
FR-32: Epic 5 - AI usage controls and data protection
FR-33: Epic 3 - Public browsing
FR-34: Epic 3 - Conventional filters and sorting
FR-35: Epic 6 - Natural-language text search
FR-36: Epic 6 - Interpretation transparency and unknown data
FR-37: Epic 6 - Voice search
FR-38: Epic 6 - Search availability and abuse control
FR-39: Epic 3 - Listing detail with provenance
FR-40: Epic 3 - Favourites
FR-41: Epic 3 - Agency public profile and Agency Listings
FR-42: Epic 4 - Request an AI Price Estimate
FR-43: Epic 4 - Baseline during Listing creation
FR-44: Epic 4 - Compare Asking price with the Estimated Property Value
FR-45: Epic 4 - 5-Year Price Forecast
FR-46: Epic 4 - Unavailable and uncertain results
FR-47: Epic 4 - Concise limitation note and documented methodology
FR-48: Epic 7 - Start an Inquiry
FR-49: Epic 7 - Real-time text messages
FR-50: Epic 7 - Inbox, history and unread
FR-51: Epic 7 - Conversation privacy
FR-52: Epic 7 - Contact privacy and internal-only communication
FR-53: Epic 7 - Conversation continuity and restrictions
FR-54: Epic 9 - Report a Conversation or Message
FR-55: Epic 8 - Agency dashboard
FR-56: Epic 8 - Listing performance statistics
FR-57: Epic 8 - Agency Verification request
FR-58: Epic 8 - Verified status display
FR-59: Epic 2 - Account suspension and restrictions
FR-60: Epic 2 - Quota policy management
FR-61: Epic 10 - Account directory and review
FR-62: Epic 8 - Agency Verification review
FR-63: Epic 9 - Listing moderation
FR-64: Epic 9 - Report a Listing
FR-65: Epic 9 - Report review
FR-66: Epic 9 - Suspicious and duplicate Listing flags
FR-67: Epic 10 - Reference data management
FR-68: Epic 10 - AI and ML oversight
FR-69: Epic 10 - Audit trail
FR-70: Epic 10 - Administrator dashboard
FR-71: Epic 10 - Support requests
FR-72: Epic 11 - Sample marketplace data
FR-73: Epic 11 - Local operation
FR-74: Epic 1 - Account states and capabilities
FR-75: Epic 2 - Listing visibility and owner actions
FR-76: Epic 1 - Notifications and email delivery
FR-77: Epic 4 - Synthetic dataset and valuation model
FR-78: Epic 5 - Real-estate knowledge base
FR-79: Epic 11 - Evaluation assets and harness
FR-80: Epic 11 - Technical documentation

## Epic List

#

## Epic 1: Accounts and Secure Access

Visitors can register Individual or Agency accounts, verify their email, sign in, reset passwords and manage profiles; administrators are provisioned and use a protected admin area; account states, notifications and audit capture work. Includes scaffolding the backend and frontend repositories, the local environment and the architecture's CI gates.

**FRs covered:** FR-1, FR-2, FR-3, FR-4, FR-5, FR-6, FR-7, FR-9, FR-21, FR-74, FR-76 (profile picture and logo uploads are completed in Epic 2).

### Story 1.1: Backend repository, local environment and database roles

As a developer,
I want a runnable Django backend with a one-command local environment and safe database roles,
So that every later story starts from a reproducible, secure base. [BE]

**Acceptance Criteria:**

**Given** a fresh clone with Docker, Python 3.12 and uv installed
**When** I follow the README setup
**Then** `docker compose up` starts PostgreSQL 18 with pgvector and a dev mail catcher, both published on 127.0.0.1 only
**And** Daphne serves the Django project with a single worker, configured from environment variables.

**Given** the settings package
**When** I inspect it
**Then** separate base, local, test and prod settings exist, prod refuses to start when a secret is missing or has its default value, and no secret is committed (a secret scan runs in CI).

**Given** the database
**When** it is initialised
**Then** a migration role and a separate runtime role exist, the runtime role has no DDL rights (a test proves it cannot alter a table), and migrations run only with the migration role.

**Given** the project
**When** I run the tests and CI
**Then** pytest passes against a test database including a `GET /api/v1/health` test, CI runs lint and tests, dependencies are locked with hashes and a dependency audit runs.

### Story 1.2: Layered app skeleton and import contracts

As a developer,
I want the layered app skeleton enforced by tooling,
So that the architecture's boundaries hold from the first commit. [BE]

**Acceptance Criteria:**

**Given** the repository layout
**When** I list `apps/`
**Then** `kernel`, `catalog`, `identity`, `listings`, `valuation`, `search`, `assistant`, `messaging`, `governance` and `presentation/{api,adminapi,realtime}` exist, each domain app with `services`, `selectors` and `events` modules (AD-1).

**Given** the import-linter configuration
**When** it runs
**Then** the exhaustive `layers` contract (kernel, catalog, identity, listings, tier-4 apps, governance, presentation) passes and a deliberate upward import in a test fixture makes it fail
**And** a tier-4 independence contract passes and fails when one tier-4 app imports another (AD-2).

**Given** the forbidden-import contracts
**When** they run
**Then** no domain app imports another app's `models`, no emitting app imports a subscriber, and `assistant` never imports `listings.models`, each proven by a failing fixture
**And** a lint test fails any serializer using `fields = "__all__"` (AD-3).

**Given** the AD-14 assumption that synchronous views share one thread under ASGI
**When** the scaffold spike runs one sync and one async view
**Then** the observed behaviour is recorded in `docs/architecture-notes.md`
**And** if the assumption is false, AD-14 is flagged for amendment before the messaging epic.

### Story 1.3: API contract, error envelope and structured logging

As a frontend developer,
I want a generated, committed API contract with a uniform error shape,
So that the frontend can rely on typed endpoints and consistent errors. [BE]

**Acceptance Criteria:**

**Given** the backend under `/api/v1/`
**When** I generate the contract
**Then** drf-spectacular produces `openapi.yaml`, which is committed
**And** CI fails when the committed file is stale (AD-16).

**Given** any error (validation, not found, forbidden, throttled, stale revision)
**When** the API responds
**Then** the body is `{error:{code,message,fields,request_id}}` with a stable code from a catalogue published in the schema
**And** validation and exception bodies never echo submitted private values (AD-26).

**Given** any request
**When** it is logged
**Then** the log line is structured JSON with a `request_id` and only allow-listed fields
**And** a test submits a body containing a phone, email and address and finds none of them in the log output.

**Given** a list endpoint
**When** the shared cursor-pagination class is used
**Then** results are deterministic and paginated by cursor, verified by a test endpoint.

**Given** every endpoint added later
**When** a request is logged
**Then** a global middleware test proves only allow-listed fields are logged, so no per-endpoint test is needed
**And** the error code catalogue is published in `openapi.yaml` and a new code requires a schema change.

### Story 1.4: Security baseline

As a platform owner,
I want secure defaults configured once,
So that every response and host check is safe by default (AD-17, NFR-2). [BE]

**Acceptance Criteria:**

**Given** the settings
**When** the app starts
**Then** `ALLOWED_HOSTS` and trusted origins come from environment variables with no wildcard, and cookies default to HttpOnly and SameSite=Lax (Secure outside local).

**Given** any response
**When** it is returned
**Then** security headers and a Content-Security-Policy are present (a test asserts them)
**And** absolute links in emails and redirects are built from `FRONTEND_BASE_URL`, never the Host header.

**Given** the production settings
**When** I enumerate routes
**Then** the Django admin is absent outside local settings.

**Given** forwarded address headers
**When** a request arrives
**Then** they are trusted only from the configured proxy list (shared with the rate-limit story).

### Story 1.5: Frontend scaffold and generated API client

As a frontend developer,
I want a Next.js app wired to the backend contract through one proxy and one generated client,
So that pages call typed endpoints without business logic in the frontend. [FE]

**Acceptance Criteria:**

**Given** the frontend repository
**When** I install and run it with the pinned Node version
**Then** a Next.js 16 App Router app with Tailwind starts, with route groups for public pages, signed-in pages and `admin`, and lint and type checks pass.

**Given** the dev server and the backend running
**When** the browser requests `/api/*`
**Then** Next.js proxies it to Django so the browser sees one origin (AD-17).

**Given** a new backend `openapi.yaml`
**When** I run the single sync script
**Then** the typed client is regenerated into a committed directory that is never hand-edited
**And** CI fails when the generated client is out of date.

**Given** the health page
**When** it renders on the server
**Then** it fetches `/api/v1/health` through the generated client with the incoming cookie forwarded
**And** no visibility, privacy or quota logic exists in the frontend (AD-19).

**Given** the frontend repository
**When** I run the test tooling
**Then** Playwright and an accessibility checker (axe) are installed with a sample passing test on the health page
**And** later frontend stories reuse them for end-to-end and accessibility checks.

**Given** the rule for two-repository stories
**When** a story is tagged [BE+FE]
**Then** its backend half merges and regenerates `openapi.yaml` first and its frontend half consumes the regenerated client, and a heavy story may be run as two sessions.

### Story 1.6: Actor and default-deny authorisation foundation

As a platform owner,
I want every request to resolve to an Actor and every route to deny by default,
So that no endpoint or service is accidentally open (FR-21). [BE]

**Acceptance Criteria:**

**Given** any request
**When** it reaches a view
**Then** one resolver produces an Actor (kind visitor, user, admin or system; account id; request id) and no service reads ambient request state (AD-22).

**Given** a route without an explicit permission declaration
**When** the route-permission test runs over the full route list
**Then** the test fails, so DRF's default is deny and every route declares its permission.

**Given** a resource the caller may not know about
**When** an unauthorised caller requests it
**Then** the API returns not-found, and a sign-in-required endpoint returns unauthenticated to a Visitor.

**Given** a sample mutating service
**When** I call it
**Then** it takes `actor` as its first argument and enforces policy itself
**And** a documented authorisation test matrix exists that each later phase extends with its resources.

### Story 1.7: Rate-limit foundation

As a platform owner,
I want persisted rate limits keyed by client,
So that registration, sign-in and later endpoints are protected without Redis (NFR-10). [BE]

**Acceptance Criteria:**

**Given** the policy table
**When** I read it
**Then** limits are configurable by scope, window and limit, seeded with every NFR-10 row (registration, verification and reset emails, sign-in, Messages, new Conversations, favourites, open Drafts, Reports, support requests, Assistant messages, searches and estimates), each applied by the story that owns the endpoint
**And** counters persist in PostgreSQL.

**Given** a request from an authenticated account or a Visitor
**When** the client key is computed
**Then** it is the account, or the trusted-proxy client address plus the session for Visitors
**And** a forwarded address from an untrusted source is ignored (test with a spoofed `X-Forwarded-For`).

**Given** a client over its limit
**When** it calls a limited endpoint
**Then** the API returns 429 in the standard error envelope with a retry hint
**And** a shared address with other clients is throttled together on a best-effort basis, documented in the policy.

### Story 1.8: Domain events and idempotency

As a developer,
I want a post-commit event bus and an idempotency store,
So that features can emit repairable events and safely retry mutations (AD-16, AD-18). [BE]

**Acceptance Criteria:**

**Given** the kernel event bus
**When** a domain event is emitted
**Then** it dispatches with `transaction.on_commit(robust=True)`, one callback per subscriber, and subscribers register in `AppConfig.ready`
**And** emitting an event with no subscribers is a no-op.

**Given** a subscriber that fails
**When** the event dispatches
**Then** the failure is logged and neither the response nor other subscribers change (fault-injection test)
**And** no event fires for a rolled-back transaction.

**Given** the idempotency store
**When** a service calls `kernel.idempotency.claim(scope, account_id, key, request_hash)` inside its transaction
**Then** the same key and request return the original result reference, and the same key with a different request is rejected with 422 `idempotency_key_reused`
**And** keys are scoped per account, honoured for 24 hours, purged after 7 days, and only a result reference is stored, never a response body.

### Story 1.9: Runtime executor, feature flags, AI request log and retention hooks

As a developer,
I want the shared runtime primitives in the kernel,
So that heavy work, flags, AI logging and retention have one home (AD-14, AD-18, AD-20, AD-23). [BE]

**Acceptance Criteria:**

**Given** `kernel.runtime`
**When** AI, image or inference work runs
**Then** it executes on a bounded executor with a configurable concurrency limit and capacity reserved for chat and sign-in
**And** a test shows heavy work on the executor does not hold the request thread for more than 200 ms of non-database time.

**Given** the feature-flag store
**When** a flag is read or changed
**Then** it records name, state, who changed it and when, and is readable by the AI gateway and other kernel code.

**Given** the AI request log
**When** a feature records a call
**Then** it stores feature, provider, model, latency and outcome with no content.

**Given** the retention hook protocol
**When** an app registers a purge job
**Then** `run_retention [--dry-run]` runs every registered job without the kernel importing any app
**And** the command reports what each job would delete in dry-run mode.

### Story 1.10: Audit trail and reasoned private reads

As an administrator,
I want security-sensitive actions recorded immutably,
So that later moderation and admin work is accountable (FR-69 capture). [BE]

**Acceptance Criteria:**

**Given** an action
**When** a service calls the audit writer
**Then** the record is written in the same database transaction as the action, with plain UUID actor and target references and an allow-listed metadata schema
**And** a rolled-back action leaves no audit record.

**Given** the runtime database role
**When** it attempts an UPDATE or DELETE on the audit table
**Then** the database refuses, while INSERT and SELECT succeed.

**Given** earlier stories
**When** passwords change or account state changes
**Then** an audit record is written for each
**And** metadata never contains phone, email, address or message text (tested).

**Given** the reasoned-read recorder
**When** an owning app serves a private read
**Then** its `admin_read_*` selector first calls `kernel.privacy.record_private_read(actor, reason, target_ref)`, which writes the audit record and fails closed if that write fails
**And** the request is POST-only with a per-read reason in the body and targets come from a closed enum that owning apps register.

**Given** the registered targets
**When** the enumeration test runs
**Then** every registered private target is proven to use the recorder, and the test fails for an unregistered private read.

### Story 1.11: City reference data

As the platform,
I want a controlled list of cities,
So that agencies and Listings choose cities consistently. [BE]

**Acceptance Criteria:**

**Given** the catalog app
**When** it is set up
**Then** it owns a City table seeded with Karachi by a data migration and written only through catalog services (AD-3).

**Given** the public reference endpoint
**When** the frontend requests cities
**Then** the list is returned from `/api/v1/reference/cities`, and city values are database-managed strings, never OpenAPI enums
**And** administrator management of cities arrives in the platform administration epic.

### Story 1.12: Account model, states and capability matrix

As a platform owner,
I want one account-state model and capability matrix,
So that restrictions are applied consistently everywhere (FR-74). [BE]

**Acceptance Criteria:**

**Given** an account
**When** its state is evaluated
**Then** email verification, suspension and deactivation are separate conditions yielding Email-unverified, Active, Suspended or Deactivated (AD-6)
**And** `identity.can(actor, capability)` implements the FR-74 matrix.

**Given** the matrix
**When** the table-driven test runs over states and capabilities (sign in, browse, create or edit Drafts, publish, send Messages, read own Conversations, report and request support or deactivation)
**Then** each cell matches FR-74, with capabilities that depend on later FRs marked pending and activated as those FRs ship.

**Given** a Deactivated account
**When** it tries to sign in
**Then** sign-in is refused and its sessions end immediately.

**Given** a Suspended account
**When** it signs in
**Then** it sees the suspension reason, restricted actions are refused on its next request, and its sessions stay valid.

**Given** a Suspended account being reactivated
**When** its email is verified
**Then** it returns to Active, otherwise to Email-unverified
**And** `identity.lock_account(account_id)` and `is_active_owner` exist as the only lock and eligibility primitives.

**Given** the identity app
**When** the Account model is created here
**Then** it holds a case-insensitively unique email, a password hash and the email-verified, suspended and deactivated conditions, and every state change writes an audit record
**And** identity emits `account.session_ended` events on sign-out, password change and deactivation, so later real-time code can close sockets.

**Given** the FR-74 matrix cells marked pending
**When** later stories ship their capability (reports, support and deactivation requests, messaging, publication)
**Then** each of those stories activates its own cell and adds a test.

### Story 1.13: Mail port and dev mail catcher

As a developer,
I want one mail port with a local catcher,
So that every email flow is testable without external delivery (FR-76 email). [BE]

**Acceptance Criteria:**

**Given** the local environment
**When** the system sends a transactional email through the kernel mail port
**Then** it is captured by the dev mail catcher and viewable locally, never delivered externally.

**Given** a feature sending an email
**When** its transaction commits
**Then** the email is sent after commit through the event bus, and a rolled-back action sends nothing
**And** a send failure is logged without changing the response.

**Given** a link in an email
**When** it is built
**Then** it uses `FRONTEND_BASE_URL`.

### Story 1.14: Shared contact-detail patterns

As the platform,
I want one definition of phone, email and address patterns,
So that public-text checks and AI redaction never disagree (AD-9, FR-23). [BE]

**Acceptance Criteria:**

**Given** `kernel.privacy.patterns`
**When** it is used
**Then** it defines phone patterns (digits with separators, "+92" forms and spelled-out digits), email patterns and exact-address patterns, with a false-positive test set of prices, areas and counts that must pass untouched.

**Given** a text field
**When** the helper `reject_contact_details(text)` is applied
**Then** it returns an explanation naming what was found
**And** a repository test proves no other contact-data regex exists.

**Given** later features
**When** they need contact checks
**Then** Listing text, Agency profile text and AI redaction all call this module.

### Story 1.15: Individual registration

As a Visitor,
I want to register an Individual account,
So that I can both buy and sell with one account (FR-1). [BE+FE]

**Acceptance Criteria:**

**Given** the registration form
**When** I submit it with a missing or invalid full name, email, password or phone
**Then** the response lists field-level errors
**And** the password must be at least 8 characters and not a commonly used password, and the phone must be a valid Pakistani mobile or international format.

**Given** valid details
**When** I submit
**Then** an Individual account is created in the Email-unverified state with an individual profile, and I am never asked to choose a buyer or seller role
**And** the phone is stored privately and appears in no response to any other account or Visitor.

**Given** an email that is already registered (case-insensitively)
**When** someone submits it
**Then** the response is identical to a new registration and the address receives an "already registered" notice, with no disclosure of existence
**And** two accounts may share a phone number without any duplicate being reported (B23).

**Given** repeated submissions
**When** the registration limit is exceeded
**Then** the standard 429 envelope is returned
**And** the frontend shows field errors, the limit message and the success state.

### Story 1.16: Email verification

As a registered user,
I want to verify my email address,
So that I can later publish Listings and send Messages (FR-4). [BE+FE]

**Acceptance Criteria:**

**Given** a new account
**When** registration completes
**Then** a verification email is sent through the mail port with a link built from the configured frontend base URL
**And** the link is single-use and expires after 24 hours.

**Given** a valid link
**When** I open it
**Then** the account becomes email-verified and moves from Email-unverified to Active
**And** an expired or already-used link shows a clear error with the option to request a new link.

**Given** a request for a new link
**When** I exceed 5 per hour
**Then** the 429 envelope is returned.

**Given** the frontend
**When** I follow the link
**Then** a verification result page is shown for success and for each failure.

### Story 1.17: Sign-in and sign-out with session security

As an account holder,
I want to sign in and out securely,
So that my account stays protected (FR-3). [BE+FE]

**Acceptance Criteria:**

**Given** valid credentials
**When** I sign in
**Then** a new session is issued in an HttpOnly, SameSite=Lax cookie (Secure outside local) with CSRF protection
**And** `GET /api/v1/me` returns my actor summary.

**Given** wrong credentials, or an unknown email
**When** I sign in
**Then** the same generic error is returned and does not reveal whether the email exists.

**Given** 5 failed sign-ins within 15 minutes for one account and address
**When** I try again
**Then** the same generic message is returned with a throttle that clears automatically when the window ends.

**Given** a signed-in session
**When** I sign out
**Then** the session is invalidated and authenticated calls are rejected until I sign in again.

**Given** the frontend
**When** I use the sign-in and sign-out screens
**Then** CSRF handling works through the proxy and the signed-in state is reflected in the layout.

**Given** a Deactivated account
**When** it tries to sign in
**Then** sign-in is refused, and a Suspended account can sign in with restricted capabilities (FR-74)
**And** sign-out emits `account.session_ended`, and a password change also emits it.

### Story 1.18: Password reset and password change

As an account holder,
I want to reset a forgotten password and change my password,
So that I can recover and protect my account (FR-5). [BE+FE]

**Acceptance Criteria:**

**Given** the reset form
**When** I submit any email
**Then** the response is identical whether or not it is registered
**And** a registered address receives a single-use link that expires after 1 hour.

**Given** a valid reset link
**When** I set a new password meeting the policy
**Then** the password changes and the link cannot be reused.

**Given** I am signed in
**When** I change my password with my current password
**Then** my other active sessions end and the change is recorded in the audit trail.

**Given** reset emails exceed 5 per hour per account
**When** another is requested
**Then** the 429 envelope is returned.

**Given** a completed password reset
**When** it succeeds
**Then** all other sessions of the account end as well and `account.session_ended` is emitted.

### Story 1.19: Agency registration

As a Visitor representing an agency,
I want to register a business account through a dedicated form,
So that my agency has its own identity and publishing allowance (FR-2). [BE+FE]

**Acceptance Criteria:**

**Given** the Agency form
**When** I submit agency name, business email, business phone, office address, city and credentials
**Then** all are mandatory and invalid input returns field-level errors
**And** city is chosen from the City reference list of Story 1.11.

**Given** valid details
**When** I submit
**Then** an Agency account is created whose Agency Verification status is "unverified", sharing the email verification flow of Story 1.16
**And** the business phone and full office address are stored privately, while city is public.

**Given** several employees of one agency
**When** they use the account
**Then** the system does not distinguish them (a documented non-goal for per-employee seats).

**Given** the frontend
**When** I open the Agency registration page
**Then** it is separate from the Individual form and applies the same rate limits and generic duplicate-email response.

### Story 1.20: Individual profile management

As an Individual account holder,
I want to view and update my profile,
So that my details stay correct (FR-6). [BE+FE]

**Acceptance Criteria:**

**Given** I am signed in
**When** I update my name or phone
**Then** the change is saved, the phone remains mandatory and private, and only I can read or change it.

**Given** I request an email change
**When** I submit a new address
**Then** it must be verified before it replaces the old one, which stays valid until then
**And** uniqueness rules of registration apply without disclosing existing addresses.

**Given** the frontend
**When** I open my profile page
**Then** I can view and edit these fields with validation messages
**And** picture upload is not part of this story (see Epic 2).

### Story 1.21: Agency business profile

As an Agency account holder,
I want to view and update my business profile,
So that customers see accurate business details (FR-7). [BE+FE]

**Acceptance Criteria:**

**Given** I am signed in as an Agency account
**When** I update name, contact details, address or description
**Then** the changes are saved and only the Agency account can edit them.

**Given** the profile fields
**When** classification is applied
**Then** the business phone and full office address are private, while name, description and city are public fields for the later public profile.

**Given** the frontend
**When** I open the business profile page
**Then** it shows and edits those fields with validation
**And** logo upload is not part of this story (see Epic 2).

**Given** the Agency name and description
**When** they are saved
**Then** the contact-detail check of Story 1.14 rejects phone numbers and addresses with an explanation.

### Story 1.22: Administrator provisioning and protected admin API

As a platform operator,
I want administrator accounts to exist only by provisioning and to use protected endpoints,
So that privileged access is controlled (FR-9). [BE]

**Acceptance Criteria:**

**Given** the platform command
**When** I provision, disable or remove an Administrator
**Then** each is recorded in the audit trail, no default credentials exist, and a test enumerating all routes finds no registration path that creates an Administrator.

**Given** a non-administrator account or a Visitor
**When** it calls any `/api/v1/admin/*` route
**Then** access is denied by the backend regardless of the frontend, and the route test confirms every admin route requires the Administrator capability.

**Given** the admin sign-in endpoint
**When** a non-administrator uses it
**Then** it is rejected, and failed and successful administrator sign-ins are throttled where failed and recorded in the audit trail
**And** administrator passwords follow the strong policy.

**Given** an administrator session
**When** it is idle for 30 minutes or 8 hours have passed
**Then** it expires, and the administrator can sign out and change their password while signed in, with a forgotten password reset only through the provisioning command.

**Given** an Administrator account
**When** it tries to create a Listing
**Then** it is refused, because Administrators cannot own Listings.

### Story 1.23: Admin area shell

As an administrator,
I want a protected admin area in the web app,
So that all administration screens have one secure home. [FE]

**Acceptance Criteria:**

**Given** the admin route group
**When** a Visitor or a non-administrator opens it
**Then** they are sent to the admin sign-in page and cannot see any admin content.

**Given** an administrator
**When** they sign in
**Then** an empty dashboard shell with navigation placeholders is shown and the accessibility check passes
**And** the frontend contains no administration business logic.

### Story 1.24: In-app notification list

As an account holder,
I want to see notifications about events affecting my account,
So that I learn about outcomes without checking each feature (FR-76 in-app). [BE+FE]

**Acceptance Criteria:**

**Given** a feature calls `notify(account_id, kind, payload)`
**When** its transaction commits
**Then** a notification is stored for that account after commit and not if the transaction rolls back
**And** the payload never contains another account's private data.

**Given** I am signed in
**When** I request my notifications
**Then** they are returned newest first with cursor pagination and read or unread state, and I can mark them read.

**Given** the frontend
**When** I open the notifications page
**Then** the list and unread count show
**And** new Messages are not notifications (they use the unread indicator in Epic 7), and email alerts for offline recipients are not provided.

**Given** later epics
**When** their events occur
**Then** each registers its own notification kinds with the feature that produces it.

## Epic 2: Create and Publish Listings

Individual and Agency owners create Drafts manually, with images, validate and publish them within the 2 or 10 monthly quota, and manage the Listing lifecycle; administrators configure quotas and suspend accounts. This epic also completes profile picture and Agency logo uploads from Epic 1.

**FRs covered:** FR-11, FR-12, FR-13, FR-14, FR-15, FR-16, FR-17, FR-18, FR-19, FR-20, FR-22, FR-23, FR-59, FR-60, FR-75 (owner side); completes FR-6 and FR-7.

**Given** the notification kinds
**When** the registry is read
**Then** it lists listing restricted, removed and restored, account suspended and reactivated, Verification outcome, Report outcome, support request closed and abandoned-Draft warning, each registered by the story that produces it (a deactivation approval goes by email)
**And** another account requesting my notifications gets not-found and the authorisation matrix gains the notification resource.

## Epic 2: Create and Publish Listings

Individual and Agency owners create Drafts manually, with images, validate and publish them within the 2 or 10 monthly quota, and manage the Listing lifecycle; administrators configure quotas and suspend accounts. This epic also completes profile picture and Agency logo uploads from Epic 1.

**FRs covered:** FR-11, FR-12, FR-13, FR-14, FR-15, FR-16, FR-17, FR-18, FR-19, FR-20, FR-22, FR-23, FR-59, FR-60, FR-75 (owner side); completes FR-6 and FR-7.

### Story 2.1: Start a Draft with purpose, Category and Subtype

As an Individual or Agency account holder,
I want to start a new Listing Draft by choosing sale or rent, a Category and a Subtype,
So that I begin from the right structure for my property (FR-11). [BE+FE]

**Acceptance Criteria:**

**Given** I am signed in with an Active or Email-unverified account
**When** I choose sale or rent, a Category (House, Apartment, Plot, Commercial) and a Subtype
**Then** a Draft is created owned by my account (Agency-created Drafts belong to the Agency), in status draft with revision 1 and the current registry version recorded
**And** a Subtype that does not belong to the chosen Category is rejected with a field-level error, and Plot File is a distinct Subtype of Plot.

**Given** the registry
**When** the backend starts
**Then** the Field registry YAML in `catalog` is loaded and served at `GET /api/v1/registry` for the frontend to render forms from
**And** the loader rejects a private-class field flagged searchable, sortable or valuation_input (tested), and the registry initially contains only the fields introduced so far.

**Given** a Suspended or Deactivated account
**When** it tries to create a Draft
**Then** the request is refused according to the FR-74 matrix.

**Given** the frontend
**When** I open "Create listing"
**Then** I see the manual path (the AI-assisted path is added in Epic 5) and the Draft appears in my Drafts.

**Given** an Administrator account
**When** it tries to create a Draft
**Then** it is refused, because Administrators cannot own Listings.

**Given** I already have 20 open Drafts
**When** I start another
**Then** the 429 envelope is returned (NFR-10).

**Given** a Listing resource
**When** it is returned by the API
**Then** it has the shape `{id, status, visibility, revision, registry_version, fields, media, amenities}` with `fields` keyed by flat registry ids (AD-24), asserted by a contract test.

### Story 2.2: Conflict-safe editing with title, description and price

As a Listing owner,
I want to edit my Draft's title, description and Asking price without losing newer changes,
So that two sessions or a later assistant never silently overwrite my work (FR-22, FR-12 part). [BE+FE]

**Acceptance Criteria:**

**Given** a Draft I own
**When** I send `{base_revision, fields}` with title, description, Asking price, price basis (total, per unit, or rental period) and, for rent, a rental period
**Then** `listings.services.apply_updates(actor, ...)` validates against the registry, saves, increments the revision and writes a revision record with origin "manual"
**And** saving never requires complete data, and money is stored as `NUMERIC` and serialised as a string in PKR.

**Given** my update carries an older `base_revision` than the stored one
**When** I submit it
**Then** the API returns 409 with the current data and nothing is overwritten
**And** the same rule applies to edits of a Published listing and to simultaneous sessions.

**Given** a Draft created under an earlier registry version
**When** I next edit it
**Then** it is revalidated against the current registry and published Listings are not altered.

**Given** another account or a Visitor
**When** it requests or edits my Draft
**Then** the API returns not-found.

**Given** the frontend
**When** I edit these fields
**Then** it shows validation errors and, on a 409, the current data with the option to reload.

**Given** a saved field
**When** its provenance is stored
**Then** each field carries its origin (manual or ai) and a confirmed flag, both set by the service from the actor and call path, never from the client payload or model output (FR-22, AD-10).

**Given** a field flagged `valuation_input` in the registry changes
**When** the edit commits
**Then** `listings.services` emits `listing.model_inputs_changed` through the kernel event bus, and emitting with no subscribers is a no-op.

**Given** a title or description
**When** I save it
**Then** the contact-detail check of Story 1.14 rejects phone numbers and addresses with an explanation
**And** prices, areas and counts in legitimate text are not rejected.

**Given** the frontend receives a 409
**When** I reload the current data
**Then** my unsaved input is preserved so I can reapply it.

### Story 2.3: Area and measurement units

As a Listing owner,
I want to state my property's area in the unit I know,
So that buyers see it correctly and search can compare it (FR-12 part). [BE+FE]

**Acceptance Criteria:**

**Given** a Draft
**When** I set area value and unit (square feet, square yards, marla, kanal or acre)
**Then** the values are saved as `NUMERIC` with an explicit unit and a derived normalised area where the conversion is certain.

**Given** the unit is marla
**When** I set a conversion basis (225, 250 or 272.25 square feet)
**Then** the normalised area is computed
**And** if the basis is unknown Publication is not blocked, but the Listing is excluded from normalised-area filtering and its estimate will be unavailable.

**Given** publication validation
**When** area is zero or negative
**Then** it is rejected.

**Given** the frontend
**When** I choose marla
**Then** I am asked for the basis and told what happens if I leave it unknown.

### Story 2.4: House and Apartment details

As an owner of a house or apartment,
I want to enter the details buyers expect,
So that my Listing is complete and searchable (FR-12 part). [BE+FE]

**Acceptance Criteria:**

**Given** a House or Apartment Draft
**When** I edit its details
**Then** bedrooms and bathrooms are required for Publication
**And** the recommended and optional attributes from the attribute catalogue (floors, kitchens, construction year, condition, furnishing, parking spaces, occupancy status, covered area, and for apartments floor number and building floors) are accepted and never block Publication.

**Given** an attribute I leave unanswered
**When** it is saved or read
**Then** it is null and never stored as zero or false.

**Given** the form
**When** I choose House versus Apartment
**Then** only the fields relevant to that Subtype are shown, driven by the registry
**And** the residential detail record is created only when I first save a residential attribute.

### Story 2.5: Plot and Commercial details

As an owner of a plot or commercial unit,
I want to enter the details specific to my property type,
So that buyers see accurate information and legal claims are clearly mine (FR-12 part). [BE+FE]

**Acceptance Criteria:**

**Given** a Plot Draft (Residential Plot, Commercial Plot or Plot File)
**When** I edit its details
**Then** the Subtype and plot area are required and recommended attributes (dimensions, possession status, corner plot, road width, balloting status, electricity, water, sewerage, development status, approval authority) and optional ones are accepted
**And** a Plot File is never presented as an ordinary possession-ready plot.

**Given** a Commercial Draft (Shop or Office)
**When** I edit its details
**Then** the Subtype and area are required and recommended attributes (business use, floor, frontage for shops, condition, fit-out, parking, electricity, lift for offices, security, road access, occupancy) and optional ones are accepted.

**Given** legal, approval or possession attributes
**When** they are displayed
**Then** they carry a "seller-declared" label and never an EstateAI badge.

**Given** attributes outside the active registry, such as installment, title-document or mortgage fields
**When** a client submits them
**Then** they are rejected as unsupported (they arrive in Epic 11).

### Story 2.6: Sale and rent terms

As a Listing owner,
I want to state the commercial terms of my offer,
So that buyers and renters know what to expect (FR-12 part). [BE+FE]

**Acceptance Criteria:**

**Given** a sale Draft
**When** I edit terms
**Then** the total Asking price is required and price negotiable and possession availability are accepted
**And** terms are stored in `ListingTerms` as typed nullable columns for active registry fields.

**Given** a rent Draft
**When** I edit terms
**Then** the rental amount and an explicit rental period (day, week, month or year) are required and the system never assumes monthly
**And** security deposit, availability date and furnishing are recommended, with advance rent, minimum lease, notice period, maintenance charges, included utilities, pets allowed and occupant restrictions optional.

**Given** a rent Draft without a rental period
**When** I publish later
**Then** validation names the missing period.

**Given** deferred terms such as payment type or installments
**When** submitted
**Then** they are rejected as unsupported until Epic 11.

### Story 2.7: Public location and Private address

As a Listing owner,
I want to give the public location and keep my exact address private,
So that buyers can find the area without my address being exposed (FR-13). [BE+FE]

**Acceptance Criteria:**

**Given** the reference data
**When** I choose a city and a locality
**Then** they must come from the City and Locality lists, with a locality `kind` of area or society, Karachi and its initial localities seeded from a seed file the owner approves (open question OQ-2)
**And** society, phase, sector and block are text on the location and are part of the public location.

**Given** I add an exact street, house number or coordinates
**When** I save
**Then** they are stored in the separate private address record, readable only by me
**And** they never appear in any public response.

**Given** another account or a Visitor
**When** it requests my private address
**Then** the API returns not-found.

**Given** publication validation
**When** city or locality is missing
**Then** it is reported as a field-level error.

**Given** the frontend
**When** I fill the location
**Then** the public and private parts are visibly separated.

### Story 2.8: Amenities and seller-declared nearby facilities

As a Listing owner,
I want to declare amenities and nearby facilities,
So that buyers can filter on them (FR-14). [BE+FE]

**Acceptance Criteria:**

**Given** the amenity catalogue
**When** the system is set up
**Then** amenities exist with stable codes and a kind (on_property, community, nearby), seeded with the controlled vocabulary and the nearby types mosque, park, gym, school and hospital.

**Given** a Draft
**When** I select or clear amenities and nearby facilities
**Then** each is saved with a value state (present, explicitly none, or unanswered by absence)
**And** an unanswered item is never treated as absent.

**Given** a nearby facility
**When** it is displayed anywhere
**Then** it is labelled "seller-declared" and no distance or verification is shown or computed.

**Given** the frontend
**When** I open the amenities section
**Then** items are grouped by kind and the "seller-declared" label is visible for nearby facilities.

**Given** the seeded catalogue
**When** I list its entries
**Then** the vocabulary is exactly: utilities (electricity backup, broadband internet, waste disposal); building (double-glazed windows, central air conditioning, central heating, elevator, lobby, intercom); residential community (community lawn, swimming pool, gym, daycare, children's play area, BBQ area); community services (community mosque, medical facility, community centre); business (conference room, business centre, ATM in building); and the nearby facilities mosque, park, gym, school and hospital.

### Story 2.9: Image upload pipeline and storage

As a Listing owner,
I want to upload images safely,
So that my Listing can show my property (FR-15). [BE]

**Acceptance Criteria:**

**Given** a Draft
**When** I upload images
**Then** JPEG, PNG and WebP up to 5 MB each and up to 10 per Listing are accepted, with a pre-decode size cap, pixel and decompression-bomb limits and a type allowlist
**And** invalid files are rejected with a clear message.

**Given** an accepted image
**When** it is processed
**Then** it is re-encoded on the kernel runtime executor with all metadata stripped and stored under a random key behind a storage port (filesystem locally).

**Given** per-account storage caps of 200 MB (Individual) and 1 GB (Agency)
**When** uploads race
**Then** the cap is checked atomically and an upload over the cap is refused.

**Given** uploads that never attach to a Listing
**When** `run_retention` runs
**Then** orphaned files older than 24 hours are removed through a registered retention hook.

### Story 2.10: Media endpoint, cover image and ordering

As a Listing owner,
I want to order my images and choose a cover,
So that my Listing shows its best image first (FR-15). [BE+FE]

**Acceptance Criteria:**

**Given** several images
**When** I choose a cover and reorder
**Then** the Listing has at most one cover and a consistent order
**And** deleting the cover promotes the next image, and Publication is blocked if none remains.

**Given** a Draft image
**When** anyone requests it
**Then** it is served only by the Django media endpoint, which applies the visibility policy, and for a Draft only to its owner
**And** public serving of Published listings' images is added later in this epic, and a Draft may have no images.

**Given** the frontend
**When** I manage images
**Then** I can upload, reorder, set the cover and delete, and the accessibility check passes.

### Story 2.11: Profile pictures and Agency logos

As an account holder,
I want to upload a profile picture or an Agency logo,
So that my account has an identity (completes FR-6 and FR-7). [BE+FE]

**Acceptance Criteria:**

**Given** the Individual profile or the Agency business profile
**When** I upload a picture or logo
**Then** it passes the same pipeline and limits as Story 2.9
**And** only the owning account can change it.

**Given** an Agency logo
**When** a Visitor requests it
**Then** it is served only while the Agency account is Active
**And** a Suspended or Deactivated Agency's logo returns not-found.

**Given** an Individual profile picture
**When** another account requests it
**Then** it returns not-found (it is private to its owner).

### Story 2.12: Visibility policy and public projections

As the platform,
I want one visibility policy and public projection types,
So that hidden, draft and removed Listings never leak (FR-75, FR-21). [BE]

**Acceptance Criteria:**

**Given** the listings app
**When** visibility is evaluated
**Then** `public_listings()` and `can_view(viewer, listing)` are the only implementations: public iff status is published, not restricted and the owner is Active
**And** every other combination returns not-found to Visitors and other accounts.

**Given** a Listing
**When** a public response is built
**Then** it comes from a public projection type containing only public fields, never the private address or phone, and never a model instance.

**Given** the media endpoint
**When** a Visitor requests an image of a publicly visible Published listing
**Then** it is served with short cache headers and `nosniff`, and images of any non-visible Listing are served only to the owner.

**Given** the owner is Suspended
**When** anyone requests the Listing or its images
**Then** they are not-found until the owner is reactivated, when they are visible again.

**Given** the matrix
**When** the table-driven test runs over status, restricted flag (set by fixtures), owner state and viewer (Visitor, other account, owner, Administrator)
**Then** every cell matches FR-75
**And** restriction, removal and Administrator actions are implemented in the moderation epic.

### Story 2.13: Draft preview and management

As a Listing owner,
I want to save, reopen, preview and delete my Drafts,
So that I can work on a Listing over several sessions (FR-16). [BE+FE]

**Acceptance Criteria:**

**Given** an incomplete Draft
**When** I save it
**Then** it is stored without requiring complete data and appears in my Drafts list, and I can reopen and edit it.

**Given** a Draft
**When** I preview it
**Then** the preview is built from the public projection type, showing exactly what the public will see, never the private address or phone
**And** the estimate area is empty until the valuation feature exists.

**Given** an unpublished Draft
**When** I delete it
**Then** its data and images are removed immediately and stored files within 24 hours, and a Published listing cannot be deleted this way.

**Given** a Draft with no edits for 180 days
**When** the `run_retention` command runs
**Then** I am notified and it is deleted 30 days later unless opened
**And** the command is introduced here with this job and is extended by later epics.

### Story 2.14: Publication quota policy and allowance view

As a Listing owner,
I want to see my monthly publication allowance,
So that I know how many Listings I can still publish (FR-18 part). [BE+FE]

**Acceptance Criteria:**

**Given** the system is set up
**When** the quota policy is read
**Then** Individual accounts have 2 and Agency accounts 10 publications per calendar month in Karachi time (default policy), stored as effective-dated policy rows that cannot overlap for one account type.

**Given** counted publication events
**When** my allowance is computed
**Then** usage is the number of counted events in the current Karachi calendar month and the remaining allowance and reset date are returned
**And** the publication event table is created here and is empty until publication writes to it later in this epic.

**Given** a quota policy change
**When** it takes effect
**Then** it applies only to later publication attempts.

**Given** the frontend
**When** I open my dashboard
**Then** my allowance and remaining publications are shown.

**Given** the policy table
**When** an administrator later changes a limit
**Then** my displayed allowance reflects it for later attempts only.

### Story 2.15: Validate and publish a Listing

As a Listing owner,
I want to publish my Draft immediately once it is valid and within my quota,
So that buyers can see it without waiting for approval (FR-17, FR-18). [BE+FE]

**Acceptance Criteria:**

**Given** a Draft missing required information
**When** I publish
**Then** the API returns field-level errors naming each missing or invalid item and the Draft stays a Draft
**And** required items are purpose, Category, Subtype, title, Asking price with price basis (rental period for rent), city, locality, area with unit, at least one image, and bedrooms and bathrooms for House and Apartment.

**Given** a valid Draft and an allowance remaining
**When** I publish
**Then** it becomes a Published listing immediately with no administrator approval
**And** in one transaction the account is locked, then the listing, the `base_revision` is compared, the idempotency key is claimed, quota is checked, a counted `initial` event with the identity-field fingerprint is written, the status is set and an audit record is written.

**Given** my quota is exhausted
**When** I publish
**Then** the API refuses with a message stating the limit and the reset date, and consumes no quota.

**Given** 20 simultaneous requests against one remaining slot
**When** they run
**Then** exactly one succeeds
**And** a retry with the same `Idempotency-Key` returns the original result without a second charge, and the same key with different content returns 422.

**Given** an Email-unverified or Suspended account
**When** it publishes
**Then** it is refused with a clear reason
**And** no AI call, upload or email happens inside the transaction.

**Given** the frontend
**When** I publish
**Then** errors are shown next to fields and the remaining allowance updates.

**Given** a Draft with an AI-supplied sensitive value that is not confirmed
**When** I publish
**Then** Publication is refused naming the value (testable with a system-actor fixture)
**And** only `listings.publication` can set a Listing to published, and `set_status` rejects it.

**Given** a successful Publication
**When** the transaction commits
**Then** `listing.published` is emitted through the kernel event bus after commit, with no effect when nothing subscribes.

**Given** a failed Publication (validation, quota, state)
**When** it is refused
**Then** no quota is consumed and no event or audit side effect remains (test).

**Given** a fault injected mid-transaction
**When** Publication is interrupted
**Then** no half-applied state remains (NFR-4 fault-injection test).

### Story 2.16: Sample data foundation

As the developer,
I want a seed command and a system actor from the start,
So that every later frontend and test story has realistic data (FR-72 foundation). [BE]

**Acceptance Criteria:**

**Given** the system actor
**When** the seed command publishes Listings
**Then** it goes through `listings.publication.publish` with a `seed` event kind that consumes no quota
**And** the command can be repeated safely and removes or reseeds its own records, which are identifiable internally.

**Given** the command
**When** it runs
**Then** it creates a small set of Individual and Agency accounts and published Listings of several Categories, and later epics extend it.

### Story 2.17: Manage the Listing lifecycle

As a Listing owner,
I want to see all my Listings by status and manage them,
So that I can keep my advertising up to date (FR-19). [BE+FE]

**Acceptance Criteria:**

**Given** my Listings
**When** I open my dashboard
**Then** they are grouped by status: draft, published, archived, sold, rented and removed.

**Given** a Published listing
**When** I edit it
**Then** any non-identity field can change (for example description, price, bedrooms, amenities, images) and the edit is validated by the publication rules, refusing an edit that would make it invalid
**And** identity fields (Category, Subtype, purpose, city, locality, area with unit) cannot be edited while published, except by the one-time correction added later in this epic.

**Given** a Published listing
**When** I archive it, or mark it sold or rented
**Then** the allowed transitions only apply: published to archived, sold or rented; sold or rented to archived
**And** "archive" is the only owner withdraw action, and moving to published goes only through publication.

**Given** an owner who is Suspended
**When** they manage Listings
**Then** they may view and archive only, with no edits, republication or new Publication.

**Given** other accounts
**When** they try to edit my Listing
**Then** the API returns not-found.

**Given** an archived Listing
**When** I edit it
**Then** non-identity fields can change and identity fields can change too (they are re-evaluated at republication)
**And** the owner can view a restricted or removed Listing with its reason but not edit it, and removed Listings appear in the dashboard grouped as removed.

**Given** a Published listing edit
**When** it commits
**Then** it consumes no quota and emits `listing.model_inputs_changed` when a `valuation_input` field changed.

### Story 2.18: Republication

As a Listing owner,
I want to republish an archived Listing,
So that I can relist without cheating the quota (FR-20, FR-18). [BE+FE]

**Acceptance Criteria:**

**Given** an archived Listing
**When** I republish it
**Then** it is revalidated by the publication rules and subject to quota through `publication.republish`
**And** a sold or rented Listing must first be returned to archived.

**Given** republication in a different calendar month
**When** it succeeds
**Then** it counts toward that month's quota with a `republish_counted` event
**And** republication in the same month is free with a recorded `republish_free` event only if the identity-field fingerprint equals the last counted event's, and a changed identity field makes it counted.

**Given** a registry change that adds an identity field
**When** the data migration runs
**Then** stored fingerprints are recomputed without consuming quota (test).

**Given** a restricted Listing
**When** I try to republish
**Then** it is refused
**And** the quota tests cover counted, free, changed identity and restricted.

**Given** the frontend
**When** I republish
**Then** the outcome and remaining allowance are shown.

### Story 2.19: Correction window for locality and area

As a Listing owner,
I want to correct a mistaken locality or area once,
So that an honest typo does not cost me a slot (FR-18, B24). [BE+FE]

**Acceptance Criteria:**

**Given** a Published listing within 24 hours of a counted Publication
**When** I correct its locality or area once
**Then** it is saved without archiving and without consuming a slot, recorded as a revision and as a flag on the counted event
**And** Category, Subtype, purpose and city cannot be corrected this way.

**Given** a second correction, or a correction after 24 hours
**When** I attempt it
**Then** it is refused and archiving is required.

**Given** society, phase, sector, block and the marla basis
**When** they change
**Then** they are not identity fields and need no correction.

**Given** the frontend
**When** I open a recently published Listing
**Then** a correction action with its remaining time is offered.

### Story 2.20: Administrator quota policy management

As an administrator,
I want to view and change the monthly publication limits,
So that I can tune marketplace policy without code changes (FR-60). [BE+FE]

**Acceptance Criteria:**

**Given** an administrator
**When** I open quota management
**Then** I see the current policy for Individual and Agency accounts and usage by account.

**Given** a new limit
**When** I save it separately for Individual or Agency
**Then** it applies only to later publication attempts, without a code change
**And** each change is audited with actor, old and new values.

**Given** a non-administrator or a Visitor
**When** it calls the admin quota routes
**Then** access is denied.

**Given** the admin frontend
**When** I use the quota screen
**Then** the current limits, usage and the change form are shown with confirmation.

### Story 2.21: Administrator account suspension and reactivation

As an administrator,
I want to suspend and reactivate accounts,
So that I can stop abuse without deleting data (FR-59). [BE+FE]

**Acceptance Criteria:**

**Given** an Individual or Agency account found by email
**When** I suspend it with a reason
**Then** the account enters the Suspended state per FR-74, the reason is shown to the account at sign-in, and it cannot publish, send Messages or edit Drafts
**And** its Listings stop being publicly visible (derived) without any status change.

**Given** a suspended account
**When** I reactivate it
**Then** its Listings become visible again and it returns to Active if its email is verified, otherwise to Email-unverified.

**Given** each action
**When** it completes
**Then** an audit record is written and the account receives a notification
**And** suspension is the only restriction available in version 1.

**Given** a non-administrator
**When** it calls these routes
**Then** access is denied.

**Given** the admin frontend
**When** I look up an account by email
**Then** I can suspend or reactivate it with a reason (the full account directory arrives in Epic 10).

## Epic 3: Browse and Find Properties

Anyone can browse and filter Published listings and view provenance without signing in; signed-in users keep favourites and view Agency profiles.

**FRs covered:** FR-33, FR-34, FR-39, FR-40, FR-41.

**Given** suspension
**When** I look at where it is implemented
**Then** it is a service in `identity` and the visibility effect is derived, so it needs no cross-app governance use case, unlike deactivation in the administration epic
**And** the FR-74 cells for Suspended are covered by tests.

## Epic 3: Browse and Find Properties

Anyone can browse and filter Published listings and view provenance without signing in; signed-in users keep favourites and view Agency profiles.

**FRs covered:** FR-33, FR-34, FR-39, FR-40, FR-41.

### Story 3.1: Browse Published listings and view details

As a Visitor or signed-in user,
I want to browse Published listings and open full details without signing in,
So that I can explore the marketplace freely (FR-33). [BE+FE]

**Acceptance Criteria:**

**Given** Published listings of several owners
**When** I open the browse page
**Then** a paginated list of publicly visible Listings is returned, built from `public_listings()` and the public projection type
**And** Draft, archived, sold, rented, removed, restricted and ineligible-owner Listings never appear.

**Given** a publicly visible Listing
**When** I open its detail page
**Then** I see images, description, attributes, amenities, seller-declared nearby facilities and the public location (city, locality, society, phase, sector and block)
**And** the private address and any phone number never appear in the response.

**Given** a Listing that is not publicly visible
**When** I request it by direct link
**Then** the page returns not-found.

**Given** the pages
**When** they are requested
**Then** they are server-rendered with the cookie forwarded, usable by keyboard on desktop and mobile, .

**Given** the browse list
**When** it is built
**Then** it calls `listings.selectors.search_public(actor, spec, sort, cursor, limit)` with an empty spec, a skeleton that starts from `public_listings()` and is completed by the filter stories
**And** the public detail endpoint and its page are owned by this story.

### Story 3.2: Privacy verification gates in CI

As the platform,
I want automated checks that no private value reaches a public response or a log,
So that the privacy rules are enforced, not just stated (SM-6, NFR-3, NFR-8, AD-21, AD-26). [BE]

**Acceptance Criteria:**

**Given** the OpenAPI schema
**When** the schema-walk test runs
**Then** no private-class registry field appears in any public or other-account-facing response schema, and a deliberately added private field makes it fail.

**Given** seeded canary phone numbers, emails and private addresses in several formats (separators, "+92", spaces)
**When** every public and other-account-facing endpoint available so far is exercised
**Then** no canary appears in any response body (canaries are seeded in stored fields and private addresses, not in Message bodies).

**Given** a full test run across those entry points
**When** the log scan runs over all captured logs
**Then** no canary appears.

**Given** later epics
**When** they add endpoints
**Then** the same gates automatically cover them because they walk the route list and the schema.

### Story 3.3: Filter schema and search selector

As a buyer or renter,
I want conventional filters that behave consistently,
So that I can narrow results without AI (FR-34). [BE]

**Acceptance Criteria:**

**Given** the registry
**When** the filter schema is generated
**Then** `catalog.filters` defines `ListingFilter` as an abstract specification (city, locality, Category, Subtype, purpose, price range, area range, bedrooms, bathrooms, parking, amenities, seller-declared nearby facilities) with required and preference criteria and sort options
**And** `listings` translates it, because `catalog` cannot reference Listing models.

**Given** `listings.selectors.search_public(actor, spec, sort, cursor, limit)`
**When** a spec is executed
**Then** it is the only function turning a spec into a query and always starts from `public_listings()`
**And** required criteria combine with logical AND, preferences only affect ranking, and results are deterministic and cursor-paginated with sorting by price and listing date.

**Given** a Listing with an unanswered attribute
**When** a filter requires that attribute
**Then** the Listing is excluded (null never matches a required criterion), and a Listing without a marla basis is excluded when the filter depends on normalised area.

**Given** counts, aggregates and facets
**When** they are computed
**Then** they start from `public_listings()`.

**Given** the registry
**When** a new searchable field is added
**Then** it appears in `ListingFilter` without code changes elsewhere.

### Story 3.4: Search fixture and performance budget

As the developer,
I want a generated large fixture and a performance test,
So that the NFR-1 budgets are measured from the start (NFR-1). [BE]

**Acceptance Criteria:**

**Given** the fixture generator
**When** I run it
**Then** about 10,000 Listings without images are created repeatably, distinct from the sample marketplace data, and usable by tests.

**Given** the fixture
**When** the performance test runs
**Then** conventional search p95 is under 500 ms and Listing detail p95 is under 300 ms server time
**And** partial indexes on published records are added where the plans require, verified with EXPLAIN ANALYZE.

### Story 3.5: Filter panel and results page

As a buyer or renter,
I want to set and clear filters on the page,
So that I can find properties by my criteria (FR-34). [FE]

**Acceptance Criteria:**

**Given** the browse page
**When** I use the filter panel
**Then** every criterion of `ListingFilter` can be set and cleared, filters combine, and I can sort by price and date
**And** the AI-off experience loses no filtering capability.

**Given** the results
**When** they load
**Then** they paginate by cursor and the accessibility check passes for the panel and results.

### Story 3.6: Listing provenance on cards and detail

As a buyer,
I want to know who is advertising and how trustworthy a claim is,
So that I can judge the Listing (FR-39). [BE+FE]

**Acceptance Criteria:**

**Given** a Listing owned by an Individual or by an Agency
**When** it appears on a result card or detail page
**Then** the owner type is shown, and an Agency Listing shows the Agency name and logo.

**Given** an Agency with Verification granted
**When** its Listing is shown
**Then** a verified indicator appears
**And** unverified Agencies show none (the indicator is wired now and is switched on by Epic 8).

**Given** legal, approval or possession statements
**When** they are shown
**Then** they are labelled "seller-declared" and never carry an EstateAI badge.

**Given** the frontend
**When** I view cards and the detail
**Then** the labels are readable by screen readers.

**Given** the cards and detail
**When** the accessibility check runs
**Then** the owner-type and "seller-declared" labels are exposed to assistive technology.

### Story 3.7: Favourites

As a signed-in buyer,
I want to save and manage favourite listings,
So that I can return to properties I like (FR-40). [BE+FE]

**Acceptance Criteria:**

**Given** a publicly visible Published listing
**When** I save it
**Then** it is added to my favourites, and only Individual and Agency accounts can save (Administrators have none)
**And** a non-visible Listing cannot be newly saved.

**Given** my favourites
**When** I open the list
**Then** I can view and remove items, and removing a favourite never affects the Listing.

**Given** a favourite whose Listing later stops being visible
**When** I open my list
**Then** it shows "no longer available" without Listing details.

**Given** a Visitor
**When** they try to save
**Then** they are prompted to register or sign in and no server-side favourites exist for guests.

**Given** the favourites limit of 500 per account
**When** I try to save more
**Then** the 429 envelope is returned
**And** another account requesting my favourites gets not-found and the authorisation matrix gains the favourites resource.

### Story 3.8: Agency public profile and Agency Listings

As a buyer,
I want to view an Agency's public profile and browse its Listings,
So that I can see who I am dealing with (FR-41). [BE+FE]

**Acceptance Criteria:**

**Given** an Active Agency
**When** I open its public profile
**Then** I see its name, logo, description, Verification status and its Published listings only
**And** the business phone and full office address never appear.

**Given** a Suspended or Deactivated Agency
**When** its profile is requested
**Then** the response is not-found and its logo and verified badge are not shown.

**Given** the Agency's Listings
**When** I browse them
**Then** the same filters and pagination of Stories 3.3 and 3.5 apply within that Agency.

**Given** the frontend
**When** I follow an Agency link from a Listing
**Then** the profile page opens and is server-rendered.

## Epic 4: Price Insight

Sellers see an AI Price Estimate while listing; buyers compare the Estimated Property Value with the Asking price and see the 5-Year Price Forecast, backed by a documented synthetic dataset and model.

**FRs covered:** FR-42, FR-43, FR-44, FR-45, FR-46, FR-47, FR-77.

## Epic 4: Price Insight

Sellers see an AI Price Estimate while listing; buyers compare the Estimated Property Value with the Asking price and see the 5-Year Price Forecast, backed by a documented synthetic dataset and model.

**FRs covered:** FR-42, FR-43, FR-44, FR-45, FR-46, FR-47, FR-77.

### Story 4.1: Synthetic dataset generator and model training

As the platform,
I want a reproducible synthetic dataset and a trained model,
So that estimates have a documented, testable basis (FR-77). [BE]

**Acceptance Criteria:**

**Given** the dataset generator with a fixed seed
**When** I run it twice
**Then** the datasets are identical, its assumptions (per-locality base rates, multipliers, noise) are documented in `docs/`, and it covers Karachi localities and the supported (Category, Subtype) pairs for sale.

**Given** the training script
**When** it runs on a held-out split
**Then** it reports MAE, MAPE and R² against a median-price-per-area-by-locality baseline, with MAPE at or below 20% and better than the baseline
**And** the documentation states that the results describe the synthetic data, not the real market (SM-4a).

### Story 4.2: Model registration, feature test and forecast parameters

As the platform,
I want a registered, hash-checked model and documented forecast parameters,
So that every estimate is reproducible and safe (FR-77, AD-12). [BE]

**Acceptance Criteria:**

**Given** a trained model
**When** it is registered
**Then** a ValuationModelVersion records name, version, feature-schema version, metrics and training time, and the artifact is loaded only after a hash check
**And** a test proves the input features are a subset of registry fields flagged `valuation_input`, excluding the private address, phone and Asking price.

**Given** the forecast
**When** its scenario parameters (low, base, high growth by locality tier) are defined
**Then** they live in versioned files, are documented and are reproducible.

### Story 4.3: Request an AI Price Estimate and supportability

As a signed-in account holder,
I want to request an AI Price Estimate for my property details,
So that I get an objective reference (FR-42). [BE]

**Acceptance Criteria:**

**Given** I enter property attributes or own a Draft
**When** I request an estimate
**Then** the Estimated Property Value is returned in PKR with the model version, computed from `valuation_input` fields only
**And** the result is stored with the model-input snapshot hash, model version and feature-schema version.

**Given** the request
**When** it runs
**Then** inference executes on the bounded executor and its p95 is under 1 second on the local fixture.

**Given** more than 20 requests in an hour from one account
**When** I request another
**Then** the 429 envelope is returned
**And** a Visitor cannot trigger computation.

**Given** another account's Draft
**When** I request an estimate for it
**Then** the API returns not-found.

**Given** a canary private address and phone on the Draft
**When** features are built
**Then** neither is used (tested).

**Given** supported means sale purpose, a (Category, Subtype) pair of (House, House), (Apartment, Apartment) or (Plot, Residential Plot), a covered Karachi locality and a known marla basis where applicable
**When** the supportability test runs over all combinations
**Then** each is classified as specified, and an unsupported case returns an "estimate unavailable" state with a short reason and no number (FR-46).

**Given** each stored estimate
**When** it is saved
**Then** it keeps the non-private feature snapshot used, so it can be reproduced (FR-42).

**Given** a failed computation
**When** it occurs
**Then** it is logged in the AI request log without content.

**Given** a Draft is deleted
**When** deletion runs
**Then** its Draft estimate snapshots are removed through a registered retention hook.

### Story 4.4: Baseline price in the Listing form, limitation note and About page

As a Seller,
I want to see an estimate while I create my Listing, with a clear note on what it is,
So that I can set a realistic Asking price without being misled (FR-43, FR-47, FR-46). [BE+FE]

**Acceptance Criteria:**

**Given** the Listing form
**When** I press "Get estimate"
**Then** the AI Price Estimate appears as a recommended baseline, requested by an explicit action and not on every field edit, within the estimate throttle
**And** the Asking price field is never changed by it.

**Given** a valid Draft
**When** I publish with or without an estimate, or when the estimate is unavailable
**Then** Publication succeeds, and an unavailable state shows its short reason without layout breakage.

**Given** any place an Estimated Property Value or 5-Year Price Forecast is displayed
**When** it renders
**Then** exactly one inline info affordance (an icon with a short popover) states that the figures are AI-generated approximations from Listing attributes, not verified market valuations or guaranteed returns, and no banner is shown
**And** the popover is operable by keyboard and announced by screen readers (axe).

**Given** the popover link
**When** I follow it
**Then** an "About AI Price Estimates" page states that the figures come from a model trained on synthetic Karachi data, what the estimate uses, its limitations, and that estimates approximate asking prices, not transaction prices.

**Given** the repositories
**When** the string scan runs in both repositories and over the OpenAPI labels
**Then** no user-facing string contains "demo" or "demonstration" (SM-4c, a CI test).

**Given** the Draft preview of Story 2.13
**When** an estimate exists
**Then** the preview shows how it will appear.

### Story 4.5: Estimate on publish and on edit

As a buyer,
I want every Published listing to carry an up-to-date estimate,
So that the comparison is always available when possible (FR-44). [BE]

**Acceptance Criteria:**

**Given** `listing.published` fires
**When** `valuation` handles it
**Then** the estimate is computed from the Published listing's model-input snapshot and stored, and Publication never waits for it
**And** `valuation.selectors.current_estimate` recomputes lazily when the snapshot is stale or absent.

**Given** `listing.model_inputs_changed` fires after an edit
**When** it is handled
**Then** the estimate is recomputed, and until then, or if recomputation fails, it is unavailable rather than stale.

**Given** the owner
**When** they request recomputation
**Then** it runs within the throttle, and a new model version applies to estimates computed after it is deployed.

**Given** a Visitor
**When** they read an estimate
**Then** they read the stored result without triggering computation.

**Given** a handler failure
**When** it is injected
**Then** the Publication response is unchanged (fault-injection test).

### Story 4.6: Estimated Property Value on the Listing page

As a buyer,
I want to see the Estimated Property Value beside the Asking price,
So that I can tell fair deals from overpriced ones (FR-44). [BE+FE]

**Acceptance Criteria:**

**Given** the detail page, composed in the presentation layer
**When** an estimate is available
**Then** the Asking price and the Estimated Property Value are shown side by side using the total price (a per-unit price converted by area)
**And** "about equal" shows within ±5% and above or below with a percentage otherwise.

**Given** a Listing that is not publicly visible
**When** its estimate is requested by another account
**Then** the response is not-found and the authorisation matrix gains the estimate resource.

**Given** I own the Listing
**When** I open its detail
**Then** a "recompute estimate" action is available and shows the outcome.

### Story 4.7: 5-Year Price Forecast

As a buyer,
I want to see a 5-year forecast range,
So that I can think about long-term potential (FR-45). [BE+FE]

**Acceptance Criteria:**

**Given** a supported Listing
**When** its forecast is computed
**Then** low, base and high scenarios are produced from the documented parameters, reproducible and ordered low ≤ base ≤ high
**And** the forecast is stored with the estimate.

**Given** the detail page
**When** the forecast is available
**Then** it is shown as the "5-Year Price Forecast" with its range
**And** it is never presented as a guaranteed return or validated prediction.

**Given** the estimate is unavailable
**When** the page loads
**Then** the forecast is unavailable too.

## Epic 5: AI Listing Assistant

Sellers create a Listing by conversation with RAG-backed guidance, confirmation before Publication and safe fallbacks; the epic includes the AI gateway, redaction and the knowledge base.

**FRs covered:** FR-24, FR-25, FR-26, FR-27, FR-28, FR-29, FR-30, FR-31, FR-32, FR-78.

### Story 5.1: AI ports, redaction and recorded-stub adapter

As the platform,
I want every AI call to pass typed ports that accept only redacted text,
So that private data cannot reach a provider by accident (FR-32, AD-8). [BE]

**Acceptance Criteria:**

**Given** the kernel AI ports (LLM, embeddings, speech-to-text)
**When** a caller builds a request
**Then** the ports accept only `RedactedText` produced by `redact(text, known_values)`, which scrubs the actor's exact phone, email and address first and then applies the shared patterns of Story 1.14
**And** the false-positive set (prices, areas, counts) is preserved.

**Given** the code base
**When** the import and AST checks run
**Then** only `kernel.ai` may import a provider SDK or open outbound network, with an allowlist of hosts, and a violating fixture fails.

**Given** the recorded-stub adapter defined here
**When** tests run
**Then** it is the default adapter and records every request it receives.

**Given** the egress test harness
**When** canary phone, email and address values sit in account data and in user messages
**Then** none appears in any recorded request, and the harness is parametrised so each later AI entry point adds its case.

### Story 5.2: Gateway controls: limits, flags, request log and budget

As the platform,
I want every AI call limited, flagged and logged,
So that free-tier quotas and failures are controlled (FR-32). [BE]

**Acceptance Criteria:**

**Given** any gateway call
**When** it runs
**Then** input caps (assistant message 2,000 characters, search query 500), timeouts (12 s search, 20 s assistant turn), the feature flag and per-account limits (30 assistant messages per hour) apply, and the AI request log records provider, model, latency and outcome with no content
**And** provider and validation error bodies are never logged or returned.

**Given** the persisted AI budget ledger per provider and day
**When** the daily limit nears exhaustion
**Then** at least half of the capacity stays reserved for signed-in Listing creation
**And** an exhausted budget returns the fallback outcome with a clear message.

**Given** the AI feature flag is off
**When** a feature calls the gateway
**Then** it receives the fallback outcome immediately.

### Story 5.3: External-service notice

As a user,
I want to be told once that AI features use an external service,
So that I can choose with knowledge (FR-32). [FE]

**Acceptance Criteria:**

**Given** any account or Visitor
**When** I first use any AI feature, text or voice
**Then** a short external-service notice is shown once before first use
**And** it states that audio is covered by the notice and is not redacted.

**Given** the notice
**When** I dismiss it
**Then** it does not reappear for that browser, and it is operable by keyboard (axe).

### Story 5.4: Provider adapters and availability trial

As the developer,
I want working provider adapters and a recorded availability trial,
So that the primary provider is chosen on evidence (P5 entry check). [BE]

**Acceptance Criteria:**

**Given** the Groq and Gemini adapters
**When** they implement the ports
**Then** Groq provides the LLM and Whisper speech-to-text and Gemini a second LLM, selected by configuration, and LangChain is used only inside an adapter if at all.

**Given** the registry
**When** structured-output schemas are generated
**Then** they satisfy strict mode (every property required, `additionalProperties` false, no reliance on min or max constraints) and stay well under the provider's per-request token cap, with one retry on a 400.

**Given** the trial script, kept under `kernel.ai` or outside app code so the import gate holds
**When** it runs scripted extraction prompts and a speech sample against each provider
**Then** it records provider, model, date, observed limits and the structured-output success rate in `docs/` and the primary provider is chosen from it
**And** provider terms on retention and training use are noted, with redaction recorded as the control.

### Story 5.5: Knowledge base index and embedding runtime

As the platform,
I want a local embedding runtime and a vector index,
So that knowledge retrieval works without provider calls (FR-78). [BE]

**Acceptance Criteria:**

**Given** the embedding model
**When** the runtime is set up
**Then** a pinned model's files are vendored with no runtime download, and its dimension is fixed in settings at the pgvector migration.

**Given** the KnowledgeDocument and KnowledgeChunk tables owned by `catalog`
**When** I run the rebuild command
**Then** all passages are embedded locally and indexed reproducibly
**And** changing the model requires a new migration and a rebuild.

### Story 5.6: Knowledge base content

As the platform,
I want a curated, versioned set of passages,
So that the assistant can explain Pakistani property terms (FR-78). [BE]

**Acceptance Criteria:**

**Given** the knowledge sources
**When** they are loaded
**Then** at least 100 authored or explicitly reviewed passages exist, each with title, version and provenance, and no unreviewed scraped text
**And** coverage includes housing types, common amenities, measures (marla, kanal, square yard), possession, balloting, registry and inteqal terminology and guidance on describing a property.

**Given** the content
**When** it changes
**Then** the version increments and the index is rebuilt with the command of Story 5.5.

### Story 5.7: Grounded knowledge retrieval

As a Seller using the assistant,
I want explanations grounded in the knowledge base,
So that terms like marla or balloting are explained correctly (FR-28). [BE]

**Acceptance Criteria:**

**Given** a question about a term
**When** retrieval runs
**Then** the top passages are returned with their source and version, treated as untrusted data that cannot change instructions, validation or access rules
**And** the assistant never claims legal verification of approvals, titles or possession.

**Given** 40 labelled domain questions built here
**When** retrieval is measured against the local embedding model
**Then** the correct passage is in the top 3 for at least 32 of them (SM-11).

**Given** retrieval is unavailable
**When** the assistant runs
**Then** it still completes a Listing (fault-injection test).

### Story 5.8: Assistant turn endpoint, chat shell and retrieval integration

As a Seller,
I want to talk to an assistant that answers from reliable knowledge,
So that I can create a Listing by conversation (FR-24, FR-28). [BE+FE]

**Acceptance Criteria:**

**Given** I am signed in with a permitted account
**When** I choose "Create with AI"
**Then** a Draft and an Assistant session are created or opened, with one active session per Draft, recording the registry and schema versions
**And** a Suspended account cannot start one, and another account requesting my session gets not-found.

**Given** a message
**When** a turn runs
**Then** it is persisted as redacted text only, retrieval passages are injected as untrusted data that cannot change instructions, validation or access rules, Listing text is never given to the model as instructions, and AI text is rendered as plain text
**And** the turn passes through the gateway and a canary egress case is added for this entry point.

**Given** the turn
**When** it runs
**Then** a progress indicator is shown, the assistant turn p90 is within 15 seconds when providers are healthy, and I can switch to the manual form on the same Draft at any time.

**Given** a prompt-injection attempt inside a message or a passage
**When** the turn runs
**Then** instructions, validation and access rules are unchanged (test).

**Given** a Draft is deleted
**When** deletion runs
**Then** its Assistant history is removed through a registered retention hook.

### Story 5.9: Extraction and applying updates

As a Seller,
I want what I say turned into Listing fields,
So that the Listing fills without me typing every field (FR-25). [BE+FE]

**Acceptance Criteria:**

**Given** "240-yard double-storey house in DHA Phase 6, five bedrooms, six bathrooms, asking 6 crore"
**When** the assistant processes it
**Then** candidate field updates are produced against the registry schema and applied only through `apply_updates(actor, listing_id, base_revision, updates)` with origin set by the service
**And** values that fail validation are not saved and the user is asked to clarify.

**Given** phone, email or address typed into the chat
**When** it is processed
**Then** the assistant tells me to use the form and the value is redacted.

**Given** I edited the Draft manually while the assistant worked
**When** its update arrives with a stale revision
**Then** it is rejected with 409 and reconciled or clarified, never overwriting my change.

**Given** the assistant module
**When** the import contract runs
**Then** it never imports Listing models, and a canary egress case is added for extraction.

### Story 5.10: Follow-up question planning

As a Seller,
I want the assistant to ask only what is missing,
So that the conversation is short (FR-25). [BE+FE]

**Acceptance Criteria:**

**Given** information already supplied
**When** the assistant plans its next question
**Then** it does not ask for it again, asks missing required fields first for the Category, and then offers optional questions without forcing them.

**Given** all required fields are present
**When** the assistant has nothing further required
**Then** it offers to review the Listing.

### Story 5.11: Nearby facilities and corrections

As a Seller,
I want to mention nearby facilities and correct myself,
So that the Listing reflects what I mean (FR-26, FR-27). [BE+FE]

**Acceptance Criteria:**

**Given** "a masjid and a park nearby"
**When** the assistant processes it
**Then** those seller-declared nearby facilities are recorded through `apply_updates` and no distances are invented
**And** a mention that matches no catalogue facility is reported to me and nothing is stored.

**Given** "four bedrooms" followed by "actually five"
**When** the second message is processed
**Then** the Draft holds five and the conversation summary updates, and earlier details are preserved.

**Given** ambiguous or contradictory statements
**When** they are detected
**Then** the assistant asks a clarifying question instead of guessing.

### Story 5.12: Integrity and explicit approval

As a Seller,
I want to approve what the assistant produced before it goes live,
So that nothing is published without my consent (FR-30). [BE+FE]

**Acceptance Criteria:**

**Given** the review screen
**When** it is shown
**Then** required values and the sensitive values (Category, Subtype, purpose, Asking price, locality, area) are presented for confirmation and carry a confirmed flag once I confirm each
**And** the flag and origin are set by the service, never from model output or client payload.

**Given** an unconfirmed AI-supplied sensitive value, or an incomplete or invalid Listing
**When** I publish
**Then** Publication is refused with the specific reason, using the check built in Story 2.15
**And** no AI path can publish; only my explicit confirmation action can.

**Given** the 30 scripted scenarios including missing data
**When** they run against the stub
**Then** the assistant invents no missing facts or amenities.

### Story 5.13: Description generation

As a Seller,
I want a professional description generated from my confirmed details,
So that my Listing reads well without my writing it (FR-29). [BE+FE]

**Acceptance Criteria:**

**Given** confirmed Listing details
**When** the assistant generates a description
**Then** it is 2 to 4 short paragraphs of plain text using only confirmed details, passes the contact-detail check of Story 1.14, and every claim traces to a confirmed field (per-claim checklist)
**And** a canary egress case is added for description generation.

**Given** the generated text
**When** I edit anything manually
**Then** my manual edits survive a later assistant turn unless I change them.

**Given** the frontend
**When** I review the description
**Then** I can edit it and switch between AI and manual on the same Draft.

### Story 5.14: Resume and idempotent turns

As a Seller,
I want to leave and come back to my conversation,
So that I can finish later (FR-29). [BE+FE]

**Acceptance Criteria:**

**Given** an unfinished session
**When** I save and later resume
**Then** conversation history and Draft state are restored.

**Given** a repeated assistant turn with the same client turn identifier
**When** it is submitted
**Then** it is processed once and the original result is returned.

### Story 5.15: Graceful degradation of the assistant

As a Seller,
I want to keep working when the AI is unavailable,
So that an outage never blocks me (FR-31). [BE+FE]

**Acceptance Criteria:**

**Given** the provider or retrieval is unavailable, slow or rate-limited
**When** I send a message
**Then** I am told clearly and can continue manually, and the Draft is identical to its state before the failed turn.

**Given** an assistant call exceeding 20 seconds
**When** it times out
**Then** it is treated as unavailable.

**Given** the AI feature flag is off, or the budget is exhausted
**When** I create a Listing
**Then** the manual flow (Epic 2) works fully, verified by fault-injection tests.

## Epic 6: Natural-Language and Voice Search

Buyers search in plain words or by voice, see and edit the interpretation, and fall back to conventional filters when AI is limited.

**FRs covered:** FR-35, FR-36, FR-37, FR-38.

## Epic 6: Natural-Language and Voice Search

Buyers search in plain words or by voice, see and edit the interpretation, and fall back to conventional filters when AI is limited. It requires the AI gateway and adapters from Epic 5 (Stories 5.1 to 5.4).

**FRs covered:** FR-35, FR-36, FR-37, FR-38.

### Story 6.1: Natural-language text search

As a buyer,
I want to describe what I want in my own words,
So that I find matching Listings without setting every filter (FR-35). [BE+FE]

**Acceptance Criteria:**

**Given** a typed query sent in a POST body, never a URL
**When** it is searched
**Then** the model, through the gateway, returns a `ListingFilter` specification that `search` validates against the registry
**And** it is executed only through `listings.selectors.search_public`, so no model text ever reaches SQL (a prompt-injection test passes).

**Given** supported criteria (Category, purpose, locality, price bounds, area, bedrooms, bathrooms, parking, amenities and seller-declared nearby facilities)
**When** the query says "at least 4 bedrooms", "exactly 4" or "under 2 crore"
**Then** they mean 4 or more, exactly 4 and an upper price bound respectively.

**Given** results
**When** they are ordered
**Then** they are ranked by the number of stated preferences met and then newest first, and I can instead sort by price or date
**And** a Listing without a marla basis is excluded when the query depends on normalised area.

**Given** the frontend
**When** I use the search box
**Then** results show with their provenance and seller-declared labels.

**Given** the search entry point
**When** the egress harness of Story 5.1 runs
**Then** a canary case for natural-language search passes, and the share of model outputs rejected or repaired by validation is reported (SM-3).

**Given** the labelled query set
**When** this story completes
**Then** at least 60 English queries with their expected specifications exist, built with this feature and extended later.

**Given** a search in progress
**When** I wait
**Then** a visible progress indicator is shown and the p90 stays within 10 seconds when providers are healthy.

### Story 6.2: Interpretation transparency and unknown data

As a buyer,
I want to see and edit how my query was understood,
So that I can correct it and trust the results (FR-36). [BE+FE]

**Acceptance Criteria:**

**Given** a searched query
**When** results appear
**Then** the interpretation is shown as editable filter chips, and editing them re-runs the search through conventional filters without AI.

**Given** criteria in the query
**When** they are classified
**Then** price bounds, Category, purpose, locality and bedroom or bathroom counts stated with "at least", "exactly" or "under", or anything marked "must" or "need", are required, and all others are preferences.

**Given** an unsupported criterion such as "near a metro station"
**When** it is detected
**Then** it is named in the response and the rest of the query still runs.

**Given** a Listing that never stated parking
**When** parking is required
**Then** it is excluded, and when parking is a preference it may appear ranked lower, never treated as having none
**And** nearby-facility matches are labelled seller-declared.

### Story 6.3: Voice search

As a buyer,
I want to speak my search,
So that I can search hands-free on my phone (FR-37). [BE+FE]

**Acceptance Criteria:**

**Given** the microphone button on mobile or desktop
**When** I speak a query
**Then** the audio is transcribed through the gateway's server-side speech path and the transcript is shown and editable before searching
**And** the confirmed transcript follows the same path as Story 6.1.

**Given** English speech
**When** I search
**Then** it is supported, and Urdu or Roman Urdu are best-effort with their measured quality reported rather than promised, and English word error rate is reported, not gated.

**Given** audio
**When** it is handled
**Then** EstateAI does not store it after transcription and no account data is attached to the provider request
**And** the external-service notice has been shown to Visitors before their first voice use, and browser-native recognition is not used.

**Given** microphone permission is denied or transcription fails
**When** I try
**Then** I am told and offered the text box with nothing lost.

**Given** a browser without microphone or MediaRecorder support
**When** I open voice search
**Then** the button is hidden or disabled with the text box offered instead (NFR-9)
**And** a canary egress case for the speech entry point passes.

### Story 6.4: Search availability and abuse control

As a buyer,
I want search to keep working when AI is limited,
So that I am never stuck (FR-38). [BE+FE]

**Acceptance Criteria:**

**Given** limits of 10 searches per minute per Visitor and 30 per hour per signed-in account
**When** a client exceeds them
**Then** the 429 envelope is returned and the user is offered conventional filters with their text retained.

**Given** the global budget guard
**When** capacity runs low
**Then** capacity stays reserved for signed-in Listing creation and search falls back as above.

**Given** a search exceeding its 12-second timeout, a provider failure or the AI feature flag off
**When** I search
**Then** the same fallback applies and conventional search tests all pass (SM-8)
**And** natural-language and voice search p90 stays within 10 seconds on the local environment when providers are healthy.

## Epic 7: Real-Time Messaging

Buyers and owners hold private, real-time Conversations tied to a Listing, with history, unread state and continuity rules.

**FRs covered:** FR-48, FR-49, FR-50, FR-51, FR-52, FR-53.

## Epic 7: Real-Time Messaging

Buyers and owners hold private, real-time Conversations tied to a Listing, with history, unread state and continuity rules.

**FRs covered:** FR-48, FR-49, FR-50, FR-51, FR-52, FR-53.

### Story 7.1: Start an Inquiry

As a buyer,
I want to message a Listing's owner from the Listing,
So that I can ask questions without phone numbers (FR-48). [BE+FE]

**Acceptance Criteria:**

**Given** a publicly visible Published listing and an Active account
**When** I send an inquiry
**Then** a Conversation naming the Listing is created with my first Message, and there is at most one Conversation per Listing and initiating account, so starting again opens the existing one
**And** an Email-unverified or Suspended account cannot send.

**Given** my own Listing
**When** I try to start a Conversation
**Then** it is refused.

**Given** a Message
**When** it is sent
**Then** it is limited to 2,000 characters, empty Messages are refused, and it carries an `Idempotency-Key` that serves as its client message id so a retry never duplicates
**And** a Message is reported delivered only after it is saved.

**Given** the owner is an Agency
**When** it receives or starts Conversations
**Then** both directions work for Agency accounts.

**Given** the frontend
**When** I press "Message seller" on a Listing
**Then** an inquiry form opens, and a Visitor is prompted to sign in.

**Given** each Conversation
**When** a Message is saved
**Then** it receives a monotonic per-Conversation sequence number used for ordering and resume.

**Given** limits of 60 Messages per minute and 20 new Conversations per day per account
**When** I exceed one
**Then** the 429 envelope is returned.

**Given** an existing Conversation whose Listing is no longer visible
**When** I start again on that Listing
**Then** the existing thread opens read-only.

**Given** the Conversation resource and a seeded canary phone
**When** the authorisation matrix and the privacy gate run
**Then** another account gets not-found, the stored account phone appears in no Conversation payload, and a phone a user types into a Message is that user's own content and is not scanned
**And** the FR-74 cells for sending and reading Messages are activated with tests.

### Story 7.2: WebSocket consumer, authentication and event schema

As a participant,
I want a secure real-time connection,
So that only I receive my events (FR-49, FR-51). [BE]

**Acceptance Criteria:**

**Given** a signed-in session
**When** the WebSocket connects
**Then** the consumer authenticates the session, checks the Origin against the exact configured origins (scheme, host and port), and joins only the group of my authenticated account, never a room I name
**And** frame size, idle time and connections per account are capped.

**Given** a third account, a Visitor or a non-participating Administrator
**When** they try to receive events for a Conversation
**Then** no event is ever delivered and Conversation content is reachable by no route, WebSocket or admin view other than a reasoned Report read.

**Given** `account.session_ended` events
**When** they fire (logout, password change, deactivation, anonymisation)
**Then** my sockets are closed.

**Given** the event shapes
**When** they are committed as `events.schema.json`
**Then** CI fails when the schema is stale, and the authorisation tests run a participant, the counterpart and an intruder over both REST and the real-time path.

**Given** the AD-14 spike result of Story 1.2
**When** message send and sign-in are implemented
**Then** they are asynchronous or database-only if the spike confirmed single-thread behaviour, and the decision is recorded.

### Story 7.3: Live delivery and frontend connection

As a participant,
I want new Messages to appear instantly,
So that a conversation feels live (FR-49). [BE+FE]

**Acceptance Criteria:**

**Given** a Message is saved
**When** the transaction commits
**Then** recipients are derived from the Conversation row and the event is published after commit to the two participants only, and the WebSocket never sends
**And** a Message is reported delivered only after it is saved, and a retry never duplicates.

**Given** an online recipient
**When** a Message is sent
**Then** it appears without refreshing.

**Given** the frontend
**When** a Conversation is open
**Then** a connection hook receives events, reconnects after a drop and resumes from the last sequence number.

### Story 7.4: Chat latency under load

As the developer,
I want proof that chat stays fast while AI and image work run,
So that SM-7 holds (SM-7, NFR-1). [BE]

**Acceptance Criteria:**

**Given** five AI or image requests in flight
**When** the latency test sends Messages between two online accounts
**Then** p95 delivery is under 1 second in the local environment
**And** if it fails, the concurrency limit and executor reservation are tuned or the Redis option of AD-14 is escalated.

### Story 7.5: Inbox, history and unread state

As a participant,
I want to see all my Conversations and which are unread,
So that I never miss a reply (FR-50). [BE+FE]

**Acceptance Criteria:**

**Given** my Conversations
**When** I open the inbox
**Then** they are listed with the Listing named, last activity and unread state, an Agency's Conversations appear in its inbox, and unread clears when I open one.

**Given** a Conversation
**When** I open it
**Then** the full history is returned in order by sequence number.

**Given** a dropped connection
**When** it reconnects
**Then** the client resumes with a history request carrying the last sequence number and sees missed Messages without loss or duplication.

### Story 7.6: Conversation continuity and restrictions

As a participant,
I want my history kept when a Listing changes,
So that past discussions remain readable (FR-53). [BE+FE]

**Acceptance Criteria:**

**Given** a Listing that becomes archived, sold, rented or restricted
**When** I open an existing Conversation
**Then** the history remains readable and the thread is read-only, and the header shows "Listing no longer available" (or "Sold" or "Rented") instead of the title
**And** it accepts Messages again if the Listing becomes visible again.

**Given** a Suspended account
**When** it tries to send
**Then** it is refused with a clear reason, but it can still read its Conversations.

**Given** a counterpart who is Deactivated
**When** I try to send
**Then** I am told the user is no longer available.

## Epic 8: Agency Business Tools

Agencies get a dashboard and performance statistics and can request Verification, which administrators review and grant or revoke.

**FRs covered:** FR-55, FR-56, FR-57, FR-58, FR-62.

## Epic 8: Agency Business Tools

Agencies get a dashboard and performance statistics and can request Verification, which administrators review and grant or revoke. It builds on Epics 2, 3 and 7.

**FRs covered:** FR-55, FR-56, FR-57, FR-58, FR-62.

### Story 8.1: Agency dashboard

As an Agency owner,
I want one dashboard for my portfolio,
So that I can run my business (FR-55). [BE+FE]

**Acceptance Criteria:**

**Given** I am signed in as an Agency account
**When** I open the dashboard
**Then** I see my total Listings, Listings by status, my publication quota and remaining allowance, and the inquiries received overall and per Listing
**And** counts match the underlying Listings and Conversations.

**Given** my Listings
**When** I filter by status, Category, location and date
**Then** the filters combine.

**Given** another account
**When** it requests the dashboard
**Then** access is denied.

**Given** my Conversations
**When** I open the dashboard
**Then** it links to my inbox, which lists my Agency's Conversations (Epic 7).

### Story 8.2: Listing performance statistics

As an Agency owner,
I want to see how often my Listings are viewed,
So that I can judge their performance (FR-56). [BE+FE]

**Acceptance Criteria:**

**Given** the listings app owns the view counter
**When** a client component sends a view beacon after rendering a Published listing's detail
**Then** the count increments once per session and Listing in a 30-minute window, using a short-lived bucket keyed by a hash that expires and is purged by a retention hook
**And** owner views, server-side double fetches and known bots are not counted.

**Given** the counter
**When** the detail endpoint is measured
**Then** its p95 budget is unaffected because counting is a separate request, and only per-Listing totals are kept with no viewer identified.

**Given** my Listings
**When** I open statistics
**Then** I see their view counts, visible only to the owner.

### Story 8.3: Request Agency Verification

As an Agency owner,
I want to request Verification,
So that customers trust my agency (FR-57). [BE+FE]

**Acceptance Criteria:**

**Given** an unverified Agency
**When** I submit a request with business name, registration or licence number and one supporting document
**Then** a pending request is recorded, with at most one pending request per Agency, and the document is stored as private evidence (PDF or image, allowlisted, size-capped, served as an attachment)
**And** unverified Agencies still publish and message normally.

**Given** my request
**When** I open its status
**Then** I see unverified, pending, verified, rejected, revoked or withdrawn, and the outcome with any reason.

**Given** a pending request
**When** I withdraw it, or I am rejected or revoked
**Then** I can submit a new one.

**Given** another account
**When** it requests my evidence
**Then** the API returns not-found.

### Story 8.4: Review Verification and verified status

As an administrator,
I want to review and decide Verification requests,
So that only genuine agencies are verified and buyers can see it (FR-62, FR-58). [BE+FE]

**Acceptance Criteria:**

**Given** pending requests
**When** I open the review queue
**Then** I see them with submitted details, and viewing the evidence requires a recorded reason through the reasoned-read path of Story 1.10, each read audited.

**Given** a request
**When** I approve it, reject it with a required reason, or revoke a previous approval
**Then** the decision is recorded with my identity and time, decision history is kept, an audit record is written and the Agency is notified
**And** the Verification status is changed only through an `identity` service (governance never writes identity data directly, AD-3).

**Given** an approved Agency
**When** its public profile or Listings are shown
**Then** the verified indicator of Story 3.6 appears, no Agency-side action can set it, and a revocation removes it everywhere while the Agency's Listings stay published.

**Given** a non-administrator
**When** it calls these routes
**Then** access is denied.

## Epic 9: Moderation and Trust

Users report Listings and Conversations; administrators review Reports, restrict, remove and restore Listings, with flags and an audit trail.

**FRs covered:** FR-54, FR-63, FR-64, FR-65, FR-66 (and the administrator column of FR-75).

### Story 9.1: Report a Listing

As a user,
I want to report a misleading or inappropriate Listing,
So that the marketplace stays trustworthy (FR-64). [BE+FE]

**Acceptance Criteria:**

**Given** a publicly visible Published listing that I do not own
**When** I report it
**Then** a Report records a reason from the controlled list (misleading, duplicate, prohibited content, abusive, AI-generated content, other) and an optional description
**And** it stores a snapshot of the public-view content only, never the private address or phone.

**Given** the same Listing
**When** I report it again while mine is open
**Then** it is refused (one open Report per reporter per Listing), and the owner never sees the reporter's identity.

**Given** my own Listing, or one I cannot see
**When** I try to report it
**Then** it is refused or not-found.

**Given** a Report
**When** it is submitted
**Then** it never removes the Listing automatically, and the limit of 10 Reports per day per account applies.

**Given** a Suspended account
**When** it tries to report
**Then** it is refused (FR-74), and the report capability cell is activated with a test
**And** another account requesting my Report gets not-found and the authorisation matrix gains the Report resource.

### Story 9.2: Review Reports

As an administrator,
I want to work through Reports,
So that violations are handled consistently (FR-65). [BE+FE]

**Acceptance Criteria:**

**Given** Reports
**When** I open the queue
**Then** I can filter by reason, including AI-generated content, and view the reason, description and content snapshot, with unresolved counts shown.

**Given** a Report
**When** I move it pending to reviewing to resolved or dismissed with notes
**Then** timestamps and notes are recorded and audited, and the reporter is notified of the outcome.

**Given** at least one Report on a Listing is reviewing
**When** the Listing is shown to its owner and administrators
**Then** it shows as under review (a derived indicator)
**And** resolving or dismissing a Report never changes the Listing's restricted flag.

**Given** the under-review indicator
**When** it is shown
**Then** it is composed in the presentation layer from open Reports and never enters the listings public projection, because `listings` cannot read governance data.

### Story 9.3: Moderation Listing review

As an administrator,
I want to browse and inspect Listings,
So that I can assess reported content (FR-63). [BE+FE]

**Acceptance Criteria:**

**Given** the Listing list
**When** I filter by owner, Category, status and date
**Then** I can view all non-Draft Listings with their information and images, and Drafts only with a recorded reason through the reasoned-read path
**And** I can see per status what actions exist: archived Listings can be restricted or viewed, sold and rented Listings viewed, and Drafts cannot be removed.

**Given** a non-administrator
**When** it calls these routes
**Then** access is denied.

### Story 9.4: Moderation actions

As an administrator,
I want to restrict, remove and restore Listings,
So that serious risks are contained (FR-63, FR-75 administrator column). [BE+FE]

**Acceptance Criteria:**

**Given** a Listing
**When** I restrict it, remove it, lift a restriction, restore a removed Listing, or move a removed Listing to archived
**Then** each is a distinct action recording my identity, reason, time and outcome with an audit record
**And** restoring a removed Listing goes through `publication.restore_after_moderation` with a non-counted `restore` event and consumes no quota.

**Given** a restricted or removed Listing
**When** the public asks for it
**Then** it disappears from discovery, detail and images, and the owner is notified with the reason (and on restore)
**And** a reported Listing with no action stays visible, a restriction persists across status changes including restoration, and republication is refused while restricted.

**Given** the FR-75 table test
**When** it runs including administrator actions
**Then** every cell matches.

### Story 9.5: Report a Conversation or Message

As a participant,
I want to report abusive Messages,
So that administrators can act on them (FR-54). [BE+FE]

**Acceptance Criteria:**

**Given** a Conversation I take part in
**When** I report it
**Then** I select the Message(s) at issue and the Report stores, at submission, a snapshot of those Message(s) plus up to 5 preceding ones, taken through a `messaging.selectors` function
**And** a confirmation is shown, and the report capability cell for Conversations is activated with a test.

**Given** a Report on Messages
**When** an administrator views the snapshot
**Then** a recorded reason is required, each view is audited, and the whole Conversation is never shown by default.

**Given** the outcome
**When** the administrator decides
**Then** the Report is dismissed or resolved with notes, optionally followed by a suspension, and administrators cannot edit or delete Messages
**And** the snapshot is anonymised when a participant account is deactivated.

### Story 9.6: Suspicious and duplicate Listing flags

As an administrator,
I want advisory flags for suspicious or duplicate Listings,
So that I can review them proactively (FR-66). [BE+FE]

**Acceptance Criteria:**

**Given** Listings
**When** the rules run
**Then** a Listing is flagged as a possible duplicate when the same owner has one with the same locality and area and a near-identical price or title, and as suspicious when two or more open Reports from different reporters arrive within 7 days.

**Given** the flags
**When** I open the review list
**Then** they are shown for review and are advisory only, never changing a Listing automatically.

## Epic 10: Platform Administration

Administrators manage accounts, deactivation requests, reference data, AI oversight, the audit trail and the dashboard, with a second factor; users submit support requests.

**FRs covered:** FR-8, FR-10, FR-61, FR-67, FR-68, FR-69, FR-70, FR-71.

**Given** the flag rules
**When** they run
**Then** a `run_flags` command runs them nightly and the duplicate rule also runs after `listing.published`.

## Epic 10: Platform Administration

Administrators manage accounts, deactivation requests, reference data, AI oversight, the audit trail and the dashboard, with a second factor; users submit support requests.

**FRs covered:** FR-8, FR-10, FR-61, FR-67, FR-68, FR-69, FR-70, FR-71.

### Story 10.1: Request account deactivation or deletion

As an account holder,
I want to request that my account be closed,
So that I control my data (FR-8, request). [BE+FE]

**Acceptance Criteria:**

**Given** I am signed in
**When** I submit a deactivation or deletion request
**Then** it is recorded and my account stays Active until decided.

**Given** my request
**When** I open its status
**Then** I see pending, approved or rejected with the reason.

**Given** a Suspended account
**When** it submits a request
**Then** it is allowed (FR-74).

**Given** the request capability
**When** this story ships
**Then** the FR-74 cell for deactivation requests is activated with a test, and another account requesting my request gets not-found.

### Story 10.2: Decide and execute account deactivation

As an administrator,
I want to approve or reject closure requests and have approved ones applied safely,
So that no account is half-closed (FR-8, decision). [BE+FE]

**Acceptance Criteria:**

**Given** a request
**When** I approve or reject it with a recorded reason
**Then** a rejected request leaves the account Active and the user is notified.

**Given** an approval
**When** the governance use case runs
**Then** in one transaction it deactivates the account, emits `account.session_ended`, and calls the `AccountDataEraser` protocol implementations of `identity` and `listings`, which anonymise personal fields, archive the account's Listings (removed ones included) and leave Drafts retained but inaccessible
**And** each owner writes its audit record and an approval email is sent after commit.

**Given** the `AccountDataEraser` protocol
**When** later apps implement it
**Then** the use case calls every registered eraser in layer order.

### Story 10.3: Erasure by messaging, assistant and governance

As the platform,
I want the remaining apps to erase their share of a closed account,
So that deactivation is complete (FR-8). [BE]

**Acceptance Criteria:**

**Given** an approved deactivation
**When** the erasers of `messaging`, `assistant` and `governance` run
**Then** Messages are kept with a "deleted user" sender, Assistant history is removed, Verification evidence is deleted, favourites are removed, and Message snapshots in Reports are anonymised.

**Given** history
**When** the account is closed
**Then** quota, moderation and audit history are never deleted by cascade, and quota history does not carry to a later registration with the same email or phone (a new account starts at zero usage).

### Story 10.4: Account directory and review

As an administrator,
I want to find and review accounts,
So that I can support and police the platform (FR-61). [BE+FE]

**Acceptance Criteria:**

**Given** accounts
**When** I search and filter Individual and Agency accounts
**Then** I see their status and profile details, their Listings, their account-related Reports (Reports on their Listings, Conversations or Messages) and their deactivation requests.

**Given** private fields (phone, private address)
**When** I want to read them
**Then** I must enter a reason which is recorded, there is no bulk export of private data, and the read goes through the reasoned-read path.

**Given** a non-administrator
**When** it calls these routes
**Then** access is denied.

### Story 10.5: Reference data management

As an administrator,
I want to manage catalogue and location data,
So that new areas and amenities can be added (FR-67). [BE+FE]

**Acceptance Criteria:**

**Given** the Amenity and nearby facility catalogue and the City and Locality data
**When** I add, rename or deactivate items
**Then** the changes apply, items in use are deactivated rather than deleted, and each change is audited.

**Given** Categories, Subtypes, purposes and area units
**When** I open them
**Then** they are visible but not editable (they are fixed by the product).

**Given** the OpenAPI contract
**When** reference data changes
**Then** the contract is unaffected because database-managed values are not enums.

### Story 10.6: AI and ML oversight

As an administrator,
I want to see the state of the AI features,
So that I can react to failures (FR-68). [BE+FE]

**Acceptance Criteria:**

**Given** the AI request log
**When** I open oversight
**Then** I see feature status, aggregated failures, the deployed valuation model version and failed valuation requests without conversation content or personal data.

**Given** a malfunctioning AI feature
**When** I disable it with the feature flag
**Then** manual and conventional alternatives keep working.

**Given** AI-related Reports
**When** I need them
**Then** they are reviewed through the Report queue filter of Story 9.2.

**Given** an AI feature or the valuation feature is disabled or re-enabled
**When** the flag changes
**Then** an audit record is written with actor, flag, and old and new state.

### Story 10.7: Audit trail review

As an administrator,
I want to review the audit trail,
So that I can verify accountability (FR-69). [BE+FE]

**Acceptance Criteria:**

**Given** audit records
**When** I open the review screen
**Then** I can filter by actor, action, target and date, and records cover provisioning, sign-in and failed sign-in, password changes, suspensions, deactivation decisions, Verification decisions, moderation actions, Report status changes, quota changes, reference-data changes, AI feature disabling and every reasoned read of private data.

**Given** the product
**When** I look for edit or delete
**Then** audit records cannot be modified or deleted, and purging beyond 24 months is an operator retention job only.

### Story 10.8: Administrator dashboard

As an administrator,
I want an overview of the platform,
So that I can see what needs attention (FR-70). [BE+FE]

**Acceptance Criteria:**

**Given** the dashboard
**When** it loads
**Then** it shows total Individual accounts, total Agency accounts, total Published listings, Listings by status, pending Verification requests, unresolved Reports and platform activity (new accounts, new Listings and Messages sent in the last 7 days)
**And** the counts match the underlying data at load time (a seeded test).

### Story 10.9: Support requests

As an account holder,
I want to contact the platform team,
So that I can get help or contest a decision (FR-71). [BE+FE]

**Acceptance Criteria:**

**Given** I am signed in (including Suspended)
**When** I submit a support request
**Then** it is recorded, I see it was recorded, and a limit of 5 per day applies.

**Given** an administrator
**When** I view a request
**Then** reading its content requires a reason which is recorded, and I can mark it closed
**And** the requester is notified of the closure.

**Given** the support capability
**When** this story ships
**Then** the FR-74 cell for support requests is activated with a test, and another account requesting my request gets not-found.

### Story 10.10: Administrator second factor

As an administrator,
I want a second authentication factor,
So that the most privileged accounts are better protected (FR-10). [BE+FE]

**Acceptance Criteria:**

**Given** my administrator account
**When** I enrol a time-based one-time code
**Then** enrolment is recorded and, once enrolled, sign-in fails without a valid code.

**Given** failed codes
**When** they repeat
**Then** throttling and audit recording apply.

**Given** the baseline controls of Story 1.22
**When** the second factor is enabled
**Then** they remain in force.

**Given** a lost authenticator
**When** I need access
**Then** recovery is only through the provisioning command, and every use of the second factor (not only failures) is recorded.

**Given** the second-factor secret
**When** it is stored
**Then** it is encrypted at rest, accepts a clock-skew window of one step, and rejects a replayed code.

### Story 10.11: Retention jobs for kernel data

As a platform operator,
I want kernel-owned data purged on schedule,
So that nothing is kept longer than promised (PRD §8.3). [BE]

**Acceptance Criteria:**

**Given** the `run_retention` command of Story 1.9
**When** the kernel jobs run
**Then** idempotency records older than 7 days, notifications 90 days after being read, AI request log entries older than 90 days, application logs older than 30 days and expired rate-limit counters are purged
**And** `--dry-run` reports counts without deleting.

### Story 10.12: Retention jobs for app-owned data

As a platform operator,
I want each app to purge its own expired data,
So that retention respects ownership (PRD §8.3, AD-3). [BE]

**Acceptance Criteria:**

**Given** the registered hooks
**When** `run_retention` runs
**Then** `listings` purges expired view buckets, publication events and moderation records after 24 months; `assistant` purges history 90 days after Publication; `governance` purges Reports and evidence 24 months after resolution, Verification evidence 24 months after the decision and support requests 12 months after closure
**And** open Reports and open support requests are kept until closed.

**Given** a dry run
**When** I run it
**Then** it reports what each app would delete.

### Story 10.13: Audit purge with its own database role

As a platform operator,
I want audit records purged only by a dedicated role,
So that the audit trail stays immutable in normal operation (FR-69, AD-6). [BE]

**Acceptance Criteria:**

**Given** the audit table
**When** the runtime role attempts UPDATE or DELETE
**Then** it is refused
**And** only a dedicated purge role used by an operator command may delete records older than 24 months.

**Given** the purge command
**When** I run it with a dry run
**Then** it reports the count and deletes nothing.

## Epic 11: Integrated Demonstration and Evaluation

The whole product runs end to end on repeatable sample data, with evaluation sets, a harness and technical documentation.

**FRs covered:** FR-72, FR-73, FR-79, FR-80 (and the deferred attribute fields of FR-12); verifies NFR-2, NFR-6, NFR-9 and SM-1 to SM-11.

### Story 11.1: Full sample marketplace

As the developer,
I want one command that loads a realistic Karachi marketplace,
So that every feature can be demonstrated and tested (FR-72). [BE]

**Acceptance Criteria:**

**Given** the seed command
**When** it runs
**Then** it creates Individual and Agency accounts and Listings across all four Categories with images, localities, amenities, seller-declared nearby facilities and valuation data for the supported Categories and localities, extending the seed command of Story 2.16
**And** it consumes no account's publication quota and can be repeated safely.

**Given** the sample records
**When** I inspect them internally
**Then** they are identifiable so they can be removed or reseeded, and no end-user banner labels them.

**Given** the data
**When** I test search, valuation, messaging and moderation
**Then** it supports each, extending the generated fixture of Story 3.4.

### Story 11.2: Local operation and one-command setup

As the developer,
I want a documented one-command setup for both repositories,
So that the product runs on a machine without deployment (FR-73). [BE+FE]

**Acceptance Criteria:**

**Given** a fresh clone of both repositories on a machine meeting the documented requirements
**When** I follow the documentation
**Then** a working, seeded environment is running within about one hour
**And** the run, test and presentation steps are documented.

**Given** the environment
**When** I stop and restart it
**Then** data and configuration persist as documented.

### Story 11.3: Deferred attribute fields

As a Seller,
I want to state installment and legal details,
So that my Listing carries the full attribute catalogue (FR-12 deferred fields). [BE+FE]

**Acceptance Criteria:**

**Given** the registry
**When** the deferred fields are activated
**Then** payment type, down payment and installment fields and title, approval and mortgage declarations become registry-driven additions stored as validated `extra_attributes`
**And** they are validated, shown in the form and, where searchable, available as filters.

**Given** legal, approval and possession fields
**When** displayed
**Then** they are labelled seller-declared and never carry an EstateAI badge.

**Given** the earlier rejection of unsupported attributes (Stories 2.5 and 2.6)
**When** these fields are submitted
**Then** they are now accepted
**And** the existing `ListingFilter` and the AI extraction schema pick up the new fields from the registry without code changes elsewhere.

### Story 11.4: Floor plan and video link

As a Seller,
I want to add a floor plan and a video or virtual tour link,
So that buyers see more of my property (FR-12 deferred fields). [BE+FE]

**Acceptance Criteria:**

**Given** a Draft
**When** I add a floor plan image or a video or virtual tour link
**Then** the floor plan uses the image pipeline and media endpoint rules, and the link is validated and shown only as a link
**And** both appear in the public projection only for publicly visible Listings.

### Story 11.5: Evaluation sets

As the developer,
I want complete labelled evaluation sets,
So that the success metrics can be measured (FR-79). [BE]

**Acceptance Criteria:**

**Given** the sets
**When** they are finalised
**Then** they include at least 100 natural-language queries (at least 60 in English, plus Roman Urdu and Urdu, including negative cases), 30 scripted Assistant scenarios (including corrections, contradictions, missing data and prompt-injection attempts), exactly 40 retrieval questions and a held-out valuation split
**And** labels are reviewed by a second person where available.

### Story 11.6: Evaluation harness and live runs

As the developer,
I want a repeatable harness,
So that results are comparable across runs (FR-79). [BE]

**Acceptance Criteria:**

**Given** the harness
**When** I run it
**Then** it runs against the recorded stub for deterministic results and against live providers for SM-2, SM-3 and SM-11 and voice word error rate, recording provider, model, date and repeated runs, and writes a results report
**And** it reports the NFR-1 budgets measured on the fixture of Story 3.4.

### Story 11.7: Technical documentation

As a supervisor or future maintainer,
I want the technical documentation,
So that the architecture, data and limits are understood (FR-80). [BE+FE]

**Acceptance Criteria:**

**Given** the documentation
**When** I read it
**Then** it covers the architecture, the API contract, the synthetic data generator assumptions, the model and its limitations, forecast scenario assumptions, evaluation methodology and results, the privacy and AI-egress design and setup instructions
**And** it is consistent with the "About AI Price Estimates" page.

### Story 11.8: Security checklist and hardening verification

As the developer,
I want a documented security verification,
So that NFR-2 is evidenced (NFR-2). [BE+FE]

**Acceptance Criteria:**

**Given** an OWASP ASVS Level 1 subset checklist
**When** I walk it
**Then** each item is marked verified with its test or evidence
**And** the hardening checks pass: security headers and CSP, explicit hosts and origins, Django admin absent outside local, secrets fail-fast, separate database roles, plain-text rendering of AI and Listing text, upload limits, and CSRF behind the proxy.

### Story 11.9: Accessibility and compatibility verification

As the developer,
I want accessibility and browser checks across the main flows,
So that NFR-6 and NFR-9 are evidenced (NFR-6, NFR-9). [FE]

**Acceptance Criteria:**

**Given** search, Listing creation and chat
**When** the keyboard and axe checks run
**Then** they meet WCAG 2.1 AA for those flows.

**Given** current Chrome, Edge and Firefox on desktop and Android, and Safari on iOS best-effort
**When** the browser matrix runs
**Then** the main flows work and voice search degrades to text where unsupported.

### Story 11.10: End-to-end journeys UJ-1 and UJ-2

As the developer,
I want automated runs of the search journeys,
So that the integrated product is proven (SM-1). [BE+FE]

**Acceptance Criteria:**

**Given** a freshly seeded local environment and the recorded provider stub
**When** UJ-1 (natural-language search, estimate and favourite) and UJ-2 (voice search on mobile) run end to end
**Then** each completes with zero manual database or code interventions, in two consecutive runs.

### Story 11.11: End-to-end journey UJ-3

As the developer,
I want an automated run of the AI listing journey,
So that the assistant flow is proven (SM-1). [BE+FE]

**Acceptance Criteria:**

**Given** a freshly seeded environment and the recorded provider stub
**When** UJ-3 (AI-assisted listing, correction, estimate baseline, publication within quota, and the provider-limit fallback) runs end to end
**Then** it completes with zero manual interventions in two consecutive runs.

### Story 11.12: End-to-end journey UJ-4

As the developer,
I want an automated run of the messaging journey,
So that real-time chat is proven (SM-1). [BE+FE]

**Acceptance Criteria:**

**Given** two seeded accounts and a published Listing
**When** UJ-4 (inquiry, real-time reply, unread, history after the Listing is sold) runs end to end
**Then** it completes with zero manual interventions in two consecutive runs.

### Story 11.13: End-to-end journeys UJ-5 and UJ-6

As the developer,
I want automated runs of the agency and administration journeys,
So that governance is proven (SM-1). [BE+FE]

**Acceptance Criteria:**

**Given** a seeded environment
**When** UJ-5 (agency registration, listings, quota limit, Verification) and UJ-6 (report, restriction, quota change, Verification review) run end to end
**Then** each completes with zero manual interventions in two consecutive runs
**And** a separate smoke check against live providers is reported but not gated.

### Story 11.14: Measure and report success metrics

As the developer,
I want all success metrics measured and reported,
So that the project's claims are evidenced (SM-1 to SM-11). [BE+FE]

**Acceptance Criteria:**

**Given** the harness and tests
**When** I run the measurement
**Then** SM-1 to SM-8 and SM-11 are reported with their results and the counter-metrics SM-C1 to SM-C5 are reviewed against the checklist in the PRD
**And** results are labelled as describing synthetic data where applicable.

**Given** SM-10
**When** it is measured
**Then** the developer times, with a stopwatch, a publishable Draft for the same 5 scripted Listings once with the assistant and once with the manual form, and the median of each is reported, together with natural-language search task success compared with filters in the usability sessions.

**Given** participants are available
**When** a usability session is run
**Then** SM-9 (SUS) is reported, and otherwise it is recorded as not measured.
