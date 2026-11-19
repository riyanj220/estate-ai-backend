# EstateAI Sprint Plan

Companion to `sprint-status.yaml` (which tracks story status). Source: `_bmad-output/planning-artifacts/epics.md` (122 stories), the PRD and the architecture spine.

**How sprints work here.** You are a sole developer with Claude Code and there is no deadline, so a sprint is a *coherent batch of 3–6 sequential stories with a demonstrable goal*, not a calendar commitment. Stories inside a sprint run in the listed order; no story depends on a later one. Sprints follow the epic order and the PRD phases (P1–P8).

## Working loop for each story
1. Pick the next story in the current sprint from `sprint-status.yaml`; create or open its story file and mark it `in-progress`.
2. Build it with `bmad-build`: for a story tagged [BE+FE], backend first (merge and regenerate `openapi.yaml`), then frontend (regenerate the typed client, then consume it); [BE] stories touch only this repo, [FE] only the frontend repo.
3. Run the story's acceptance criteria as tests, then a review pass (`bmad-code-review`, ideally in a fresh context); mark `review`, then `done`.
4. At the end of a sprint, tick the exit check below, update `sprint-status.yaml`, and optionally run `bmad-retrospective` at epic boundaries.

## Gates and decisions on the critical path
- **Before Sprint 4:** run `bmad-ux` (no UX contract exists; Sprints 4 onward contain user-facing frontend work).
- **Before Sprint 7:** decide the initial Karachi locality seed list (PRD OQ-2) and the nearby-facility catalogue (OQ-1).
- **Before Sprint 21:** decide what an Agency submits for Verification (OQ-3).
- **Sprint 1 (Story 1.2):** the AD-14 thread-model spike result decides how chat send and sign-in are implemented in Sprint 19.
- **Sprint 14:** free-tier AI limits and provider terms were unconfirmed during planning; the availability trial settles the primary provider.

## Overview

| Sprint | Goal | Stories | Epic |
|---|---|---|---|
| 1 | Runnable, bounded backend | 1.1 – 1.4 (4) | 1 |
| 2 | Both repos alive, kernel primitives | 1.5 – 1.9 (5) | 1 |
| 3 | Identity foundations | 1.10 – 1.14 (5) | 1 |
| 4 | Registration and sign-in | 1.15 – 1.18 (4) | 1 |
| 5 | Profiles and admin base | 1.19 – 1.24 (6) | 1 |
| 6 | Draft building A | 2.1 – 2.6 (6) | 2 |
| 7 | Draft building B | 2.7 – 2.11 (5) | 2 |
| 8 | Visibility and publishing | 2.12 – 2.16 (5) | 2 |
| 9 | Lifecycle, republication and admin policy | 2.17 – 2.21 (5) | 2 |
| 10 | Public browsing foundations | 3.1 – 3.4 (4) | 3 |
| 11 | Discovery experience | 3.5 – 3.8 (4) | 3 |
| 12 | Valuation foundations | 4.1 – 4.4 (4) | 4 |
| 13 | Estimates in the product | 4.5 – 4.7 (3) | 4 |
| 14 | AI foundation | 5.1 – 5.4 (4) | 5 |
| 15 | Knowledge and assistant core | 5.5 – 5.8 (4) | 5 |
| 16 | Assistant behaviours | 5.9 – 5.12 (4) | 5 |
| 17 | Assistant completion | 5.13 – 5.15 (3) | 5 |
| 18 | Natural-language and voice search | 6.1 – 6.4 (4) | 6 |
| 19 | Messaging core | 7.1 – 7.3 (3) | 7 |
| 20 | Messaging completion | 7.4 – 7.6 (3) | 7 |
| 21 | Agency business tools | 8.1 – 8.4 (4) | 8 |
| 22 | Reports and moderation | 9.1 – 9.4 (4) | 9 |
| 23 | Conversation reports and deactivation | 9.5 – 10.3 (5) | 9, 10 |
| 24 | Administration tools A | 10.4 – 10.8 (5) | 10 |
| 25 | Administration tools B and retention | 10.9 – 10.13 (5) | 10 |
| 26 | Completing the catalogue | 11.1 – 11.4 (4) | 11 |
| 27 | Evidence and hardening | 11.5 – 11.9 (5) | 11 |
| 28 | Proof of the whole product | 11.10 – 11.14 (5) | 11 |

## Sprint 1: Runnable, bounded backend

**Goal:** Backend repo runs locally with enforced layer boundaries, a committed API contract and secure defaults.

**Stories:**
- 1.1 Backend repository, local environment and database roles [BE]
- 1.2 Layered app skeleton and import contracts [BE]
- 1.3 API contract, error envelope and structured logging [BE]
- 1.4 Security baseline [BE]

**Exit check:** `docker compose up` + tests green; import-linter contract fails on a bad import; `openapi.yaml` drift check works; AD-14 thread-model spike recorded.

**Notes:** Run the AD-14 spike in 1.2 and note the result before Epic 7.

## Sprint 2: Both repos alive, kernel primitives

**Goal:** Frontend scaffold with generated client; deny-by-default authorisation, rate limits, event bus, idempotency, runtime executor, flags and retention hooks exist.

**Stories:**
- 1.5 Frontend scaffold and generated API client [FE]
- 1.6 Actor and default-deny authorisation foundation [BE]
- 1.7 Rate-limit foundation [BE]
- 1.8 Domain events and idempotency [BE]
- 1.9 Runtime executor, feature flags, AI request log and retention hooks [BE]

**Exit check:** Frontend health page calls the backend through the proxy; route-permission test passes; event-bus fault-injection and idempotency tests pass.

**Notes:** Heavy [BE+FE] stories from here on are run backend first, then frontend.

## Sprint 3: Identity foundations

**Goal:** Audit trail with reasoned private reads, City data, account model and states, mail port and shared contact patterns.

**Stories:**
- 1.10 Audit trail and reasoned private reads [BE]
- 1.11 City reference data [BE]
- 1.12 Account model, states and capability matrix [BE]
- 1.13 Mail port and dev mail catcher [BE]
- 1.14 Shared contact-detail patterns [BE]

**Exit check:** Table-driven FR-74 matrix test passes; audit table append-only for the runtime role; dev mail catcher shows emails.

**Notes:** **Gate before Sprint 4:** run `bmad-ux` so the first user-facing frontend stories have a design contract.

## Sprint 4: Registration and sign-in

**Goal:** Individual registration, email verification, secure sign-in/out and password reset, end to end in the browser.

**Stories:**
- 1.15 Individual registration [BE+FE]
- 1.16 Email verification [BE+FE]
- 1.17 Sign-in and sign-out with session security [BE+FE]
- 1.18 Password reset and password change [BE+FE]

**Exit check:** A new user registers, verifies email in the catcher, signs in, resets and changes password; sessions end correctly.

## Sprint 5: Profiles and admin base

**Goal:** Agency registration, profiles, protected admin provisioning and shell, notification list. Epic 1 complete.

**Stories:**
- 1.19 Agency registration [BE+FE]
- 1.20 Individual profile management [BE+FE]
- 1.21 Agency business profile [BE+FE]
- 1.22 Administrator provisioning and protected admin API [BE]
- 1.23 Admin area shell [FE]
- 1.24 In-app notification list [BE+FE]

**Exit check:** Agency registers; admin signs in to the protected area; notifications list works; non-admin routes denied.

**Notes:** Optional Epic 1 retrospective after this sprint.

## Sprint 6: Draft building A

**Goal:** Start a Draft and fill common, residential, plot, commercial and terms details with conflict-safe saving.

**Stories:**
- 2.1 Start a Draft with purpose, Category and Subtype [BE+FE]
- 2.2 Conflict-safe editing with title, description and price [BE+FE]
- 2.3 Area and measurement units [BE+FE]
- 2.4 House and Apartment details [BE+FE]
- 2.5 Plot and Commercial details [BE+FE]
- 2.6 Sale and rent terms [BE+FE]

**Exit check:** Draft edits are revision-checked (409 on stale), provenance stored, contact details rejected in text.

**Notes:** **Decision needed before Sprint 7:** the Karachi locality seed list (OQ-2).

## Sprint 7: Draft building B

**Goal:** Locations, amenities and nearby facilities, image pipeline, media endpoint, profile pictures and logos.

**Stories:**
- 2.7 Public location and Private address [BE+FE]
- 2.8 Amenities and seller-declared nearby facilities [BE+FE]
- 2.9 Image upload pipeline and storage [BE]
- 2.10 Media endpoint, cover image and ordering [BE+FE]
- 2.11 Profile pictures and Agency logos [BE+FE]

**Exit check:** Owner can add location, amenities and images; media served only by the endpoint; storage caps enforced.

**Notes:** Needs OQ-2 (locality seed) and OQ-1 (nearby-facility catalogue) answered.

## Sprint 8: Visibility and publishing

**Goal:** One visibility policy, Draft preview, quota policy, validated atomic publication, sample-data foundation.

**Stories:**
- 2.12 Visibility policy and public projections [BE]
- 2.13 Draft preview and management [BE+FE]
- 2.14 Publication quota policy and allowance view [BE+FE]
- 2.15 Validate and publish a Listing [BE+FE]
- 2.16 Sample data foundation [BE]

**Exit check:** FR-75 viewer matrix passes; 20 parallel publishes against one slot give exactly one success; seed command publishes via system actor.

## Sprint 9: Lifecycle, republication and admin policy

**Goal:** Dashboard and lifecycle, republication with fingerprint rule, correction window, admin quota and suspension. Epic 2 complete.

**Stories:**
- 2.17 Manage the Listing lifecycle [BE+FE]
- 2.18 Republication [BE+FE]
- 2.19 Correction window for locality and area [BE+FE]
- 2.20 Administrator quota policy management [BE+FE]
- 2.21 Administrator account suspension and reactivation [BE+FE]

**Exit check:** Quota variants tested (counted, free, changed identity, correction); suspension hides Listings and reactivation restores them.

**Notes:** Optional Epic 2 retrospective.

## Sprint 10: Public browsing foundations

**Goal:** Public browse/detail, privacy gates in CI, filter schema and search selector, 10,000-Listing fixture and performance budget.

**Stories:**
- 3.1 Browse Published listings and view details [BE+FE]
- 3.2 Privacy verification gates in CI [BE]
- 3.3 Filter schema and search selector [BE]
- 3.4 Search fixture and performance budget [BE]

**Exit check:** Schema-walk and canary scans pass; search p95 < 500 ms and detail p95 < 300 ms on the fixture.

## Sprint 11: Discovery experience

**Goal:** Filter panel, provenance, favourites, Agency public profile. Epic 3 complete.

**Stories:**
- 3.5 Filter panel and results page [FE]
- 3.6 Listing provenance on cards and detail [BE+FE]
- 3.7 Favourites [BE+FE]
- 3.8 Agency public profile and Agency Listings [BE+FE]

**Exit check:** Visitor browses, filters, sees owner type; signed-in user favourites; Agency profile hidden when suspended.

**Notes:** Optional Epic 3 retrospective.

## Sprint 12: Valuation foundations

**Goal:** Synthetic dataset and model, registration and feature test, estimate requests with supportability, baseline price with limitation note and About page.

**Stories:**
- 4.1 Synthetic dataset generator and model training [BE]
- 4.2 Model registration, feature test and forecast parameters [BE]
- 4.3 Request an AI Price Estimate and supportability [BE]
- 4.4 Baseline price in the Listing form, limitation note and About page [BE+FE]

**Exit check:** Reproducible dataset; MAPE ≤ 20% and beats baseline; features limited to valuation_input fields; one info affordance per display.

## Sprint 13: Estimates in the product

**Goal:** Estimate on publish/edit, comparison on the Listing page, 5-Year Price Forecast. Epic 4 complete.

**Stories:**
- 4.5 Estimate on publish and on edit [BE]
- 4.6 Estimated Property Value on the Listing page [BE+FE]
- 4.7 5-Year Price Forecast [BE+FE]

**Exit check:** Estimates recompute after edits and never block Publication; low ≤ base ≤ high reproducible.

**Notes:** Optional Epic 4 retrospective.

## Sprint 14: AI foundation

**Goal:** Typed AI ports with redaction and stub, gateway limits and budget, external-service notice, provider adapters and availability trial.

**Stories:**
- 5.1 AI ports, redaction and recorded-stub adapter [BE]
- 5.2 Gateway controls: limits, flags, request log and budget [BE]
- 5.3 External-service notice [FE]
- 5.4 Provider adapters and availability trial [BE]

**Exit check:** Canary egress harness passes; budget ledger enforced; trial recorded and primary provider chosen.

**Notes:** Check free-tier limits and provider terms during the trial (they were unconfirmed).

## Sprint 15: Knowledge and assistant core

**Goal:** Embedding runtime, knowledge base content, grounded retrieval, assistant turn endpoint with chat shell.

**Stories:**
- 5.5 Knowledge base index and embedding runtime [BE]
- 5.6 Knowledge base content [BE]
- 5.7 Grounded knowledge retrieval [BE]
- 5.8 Assistant turn endpoint, chat shell and retrieval integration [BE+FE]

**Exit check:** Retrieval hits top-3 on at least 32 of 40 questions; assistant turn works end to end on the stub.

## Sprint 16: Assistant behaviours

**Goal:** Extraction, follow-up planning, nearby facilities and corrections, integrity and explicit approval.

**Stories:**
- 5.9 Extraction and applying updates [BE+FE]
- 5.10 Follow-up question planning [BE+FE]
- 5.11 Nearby facilities and corrections [BE+FE]
- 5.12 Integrity and explicit approval [BE+FE]

**Exit check:** 30 scripted scenarios pass on the stub; unconfirmed sensitive values block Publication.

## Sprint 17: Assistant completion

**Goal:** Description generation, resume and idempotent turns, graceful degradation. Epic 5 complete.

**Stories:**
- 5.13 Description generation [BE+FE]
- 5.14 Resume and idempotent turns [BE+FE]
- 5.15 Graceful degradation of the assistant [BE+FE]

**Exit check:** Manual flow works with AI off; failed turn leaves the Draft unchanged.

**Notes:** Optional Epic 5 retrospective.

## Sprint 18: Natural-language and voice search

**Goal:** Text search, interpretation chips, voice search, availability and abuse control. Epic 6 complete.

**Stories:**
- 6.1 Natural-language text search [BE+FE]
- 6.2 Interpretation transparency and unknown data [BE+FE]
- 6.3 Voice search [BE+FE]
- 6.4 Search availability and abuse control [BE+FE]

**Exit check:** Fallback to filters on throttle/timeout; transcript editable before search; 60 English queries labelled.

**Notes:** Optional Epic 6 retrospective.

## Sprint 19: Messaging core

**Goal:** Start an Inquiry with sequence numbers and limits, secure WebSocket consumer and event schema, live delivery.

**Stories:**
- 7.1 Start an Inquiry [BE+FE]
- 7.2 WebSocket consumer, authentication and event schema [BE]
- 7.3 Live delivery and frontend connection [BE+FE]

**Exit check:** Intruder tests pass over REST and WebSocket; messages appear without refresh.

**Notes:** Apply the AD-14 spike result here.

## Sprint 20: Messaging completion

**Goal:** Chat latency under load, inbox and resume, continuity rules. Epic 7 complete.

**Stories:**
- 7.4 Chat latency under load [BE]
- 7.5 Inbox, history and unread state [BE+FE]
- 7.6 Conversation continuity and restrictions [BE+FE]

**Exit check:** p95 delivery < 1 s with five AI/image requests in flight; read-only thread when the Listing is not visible.

**Notes:** Optional Epic 7 retrospective.

## Sprint 21: Agency business tools

**Goal:** Dashboard, view statistics, Verification request and review with verified indicator. Epic 8 complete.

**Stories:**
- 8.1 Agency dashboard [BE+FE]
- 8.2 Listing performance statistics [BE+FE]
- 8.3 Request Agency Verification [BE+FE]
- 8.4 Review Verification and verified status [BE+FE]

**Exit check:** Agency requests, admin approves with reasoned evidence read, badge appears and disappears on revocation.

**Notes:** Needs OQ-3 (Verification evidence). Optional Epic 8 retrospective.

## Sprint 22: Reports and moderation

**Goal:** Report a Listing, review Reports, moderation browsing and actions.

**Stories:**
- 9.1 Report a Listing [BE+FE]
- 9.2 Review Reports [BE+FE]
- 9.3 Moderation Listing review [BE+FE]
- 9.4 Moderation actions [BE+FE]

**Exit check:** Restrict, remove, restore and lift are distinct; restore consumes no quota; under-review indicator composed in presentation.

## Sprint 23: Conversation reports and deactivation

**Goal:** Report Messages with stored snapshots, flags, deactivation requests and the cross-app deactivation use case, erasers.

**Stories:**
- 9.5 Report a Conversation or Message [BE+FE]
- 9.6 Suspicious and duplicate Listing flags [BE+FE]
- 10.1 Request account deactivation or deletion [BE+FE]
- 10.2 Decide and execute account deactivation [BE+FE]
- 10.3 Erasure by messaging, assistant and governance [BE]

**Exit check:** Deactivation is one transaction; erasers run for all apps; quota history does not carry over.

**Notes:** Optional Epic 9 retrospective.

## Sprint 24: Administration tools A

**Goal:** Account directory, reference data, AI/ML oversight, audit review, administrator dashboard.

**Stories:**
- 10.4 Account directory and review [BE+FE]
- 10.5 Reference data management [BE+FE]
- 10.6 AI and ML oversight [BE+FE]
- 10.7 Audit trail review [BE+FE]
- 10.8 Administrator dashboard [BE+FE]

**Exit check:** Private reads require reasons and are audited; reference-data changes audited; AI feature flag disables features safely.

## Sprint 25: Administration tools B and retention

**Goal:** Support requests, second factor, kernel and app retention jobs, audit purge role. Epic 10 complete.

**Stories:**
- 10.9 Support requests [BE+FE]
- 10.10 Administrator second factor [BE+FE]
- 10.11 Retention jobs for kernel data [BE]
- 10.12 Retention jobs for app-owned data [BE]
- 10.13 Audit purge with its own database role [BE]

**Exit check:** `run_retention --dry-run` reports every table; audit purge only by its own role.

**Notes:** Optional Epic 10 retrospective.

## Sprint 26: Completing the catalogue

**Goal:** Full sample marketplace, local operation docs, deferred attribute fields, floor plan and video link.

**Stories:**
- 11.1 Full sample marketplace [BE]
- 11.2 Local operation and one-command setup [BE+FE]
- 11.3 Deferred attribute fields [BE+FE]
- 11.4 Floor plan and video link [BE+FE]

**Exit check:** Fresh clone reaches a seeded environment; deferred fields appear in forms, filters and AI schema without code changes elsewhere.

## Sprint 27: Evidence and hardening

**Goal:** Evaluation sets, harness and live runs, technical documentation, security checklist, accessibility and compatibility checks.

**Stories:**
- 11.5 Evaluation sets [BE]
- 11.6 Evaluation harness and live runs [BE]
- 11.7 Technical documentation [BE+FE]
- 11.8 Security checklist and hardening verification [BE+FE]
- 11.9 Accessibility and compatibility verification [FE]

**Exit check:** Harness report produced; ASVS L1 subset checklist verified; WCAG 2.1 AA for search, creation and chat.

## Sprint 28: Proof of the whole product

**Goal:** End-to-end journeys UJ-1 to UJ-6 and measured success metrics. Epic 11 complete.

**Stories:**
- 11.10 End-to-end journeys UJ-1 and UJ-2 [BE+FE]
- 11.11 End-to-end journey UJ-3 [BE+FE]
- 11.12 End-to-end journey UJ-4 [BE+FE]
- 11.13 End-to-end journeys UJ-5 and UJ-6 [BE+FE]
- 11.14 Measure and report success metrics [BE+FE]

**Exit check:** Each journey passes twice with zero manual interventions; SM-1 to SM-11 reported; SM-9 if participants exist.

**Notes:** Final retrospective and the defense-readiness review.
