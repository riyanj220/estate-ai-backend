# EstateAI Decision Register

Status: consolidated, pre-validation. Last updated 2026-11-19 (revision 6 adds A12 database hosting; revision 5 (adds R17): adds B17-B25 from PRD validation; sole developer, no fixed deadline, professional AI-estimate presentation).
Sources: approved FYP proposal, Actors & Functional Requirements, Domain Modeling & Database Architecture (all in `docs/references/`, unchanged), plus the team's confirmations in this planning session.

**Precedence:** latest confirmed decisions > Functional Requirements / Domain doc > approved proposal text where they conflict on technology. The proposal remains the authority for the four committed features. The proposal's Gantt chart stack (Angular, AWS/Azure) is ignored.

**Status legend**
- **CONFIRMED**: explicitly decided by the team.
- **APPROVED**: listed by the team as previously approved architecture.
- **DEFAULT**: recommended earlier, not objected to; carried into the PRD and architecture unless the team objects.
- **DEFERRED**: architecture-level detail, does not affect PRD content.

---

## 1. Product and business decisions

| ID | Decision | Status |
|---|---|---|
| B1 | One product, two repos. This repo (`estate-ai-backend`) is backend only. Frontend (Next.js, React, Tailwind, incl. custom admin dashboard) is a separate repo. PRD and architecture cover the whole product. | CONFIRMED |
| B2 | The four committed features stay: RAG listing assistant, ML price estimate + 5-year forecast, text/voice natural-language search, real-time chat. | CONFIRMED |
| B3 | Three account types: Individual (buy and sell), Agency (own registration, dashboard, higher quota, optional verification; fully in scope), Administrator (provisioned only, no public registration, dedicated protected Next.js routes, Django enforces authorization). | CONFIRMED |
| B4 | Publication quotas per calendar month: Individual 2, Agency 10. Administrator-configurable. Existing rules for publication, republication and idempotency apply. | CONFIRMED |
| B5 | Initial supported city: Karachi. City and locality structure must be extensible. | CONFIRMED |
| B6 | Phone number mandatory at individual and agency registration. Stored privately, never shown publicly. | CONFIRMED |
| B7 | Property categories: House, Apartment, Plot, Commercial, with the previously agreed subtypes (Residential Plot, Commercial Plot, Plot File, Shop, Office). | CONFIRMED |
| B8 | Nearby facilities (mosque, park, gym, school, hospital) are structured, seller-declared attributes chosen during manual or AI-assisted creation and used by natural-language search. No distance calculation, geographic verification, PostGIS or Places API. UI labels them "seller-declared". | CONFIRMED |
| B9 | Payments and subscriptions are out of scope. | CONFIRMED |
| B10 | UI language is English only. | CONFIRMED |
| B11 | No fixed deadline. The goal is a complete, polished, well-engineered FYP application, not a rushed MVP. All agreed functional requirements remain in scope unless explicitly excluded. Phased implementation exists for logical dependencies and manageable tasks, not for cutting scope. (Supersedes the earlier one-month target.) | CONFIRMED |
| B12 | Seller asking price is always separate from the ML estimate. Valuation is never required to publish. | CONFIRMED |
| B13 | Valuation and the 5-year forecast use synthetic data for ML training, testing and forecasting. UI terms: "AI Price Estimate", "Estimated Property Value", "5-Year Price Forecast". No repeated "Demo" labels and no intrusive disclaimer banners; a concise, unobtrusive explanation of prediction limitations is shown where necessary. Synthetic data, assumptions, model limitations and evaluation methodology are documented in the technical documentation. The product never claims estimates are verified market valuations or guaranteed returns. | CONFIRMED |
| B14 | Previously finalized FR and domain rules stand: immediate publish after validation (no pre-approval), no public phone numbers, internal-only messaging, optional admin-granted agency verification, reports do not auto-remove listings, AI never invents facts and needs explicit approval, one canonical draft for manual and AI creation, private exact addresses. | CONFIRMED |
| B15 | Sole developer (Riyan) implements EstateAI with Claude Code. Work is planned as small, testable, sequential tasks. Frontend and backend remain separate repositories planned together. | CONFIRMED |
| B16 | EstateAI is an academic FYP prototype, not a commercial real estate service. | CONFIRMED |
| B17 | Quota identity rule: free same-month republication applies only if the Listing's identity fields (Category, Subtype, purpose, city, locality, area with unit) are unchanged since the counted Publication; otherwise the next Publication consumes a slot. Identity fields cannot be edited while a Listing is published. | CONFIRMED |
| B18 | One lifecycle/visibility rule table governs who can see a Listing and what the owner and Administrator can do, by Listing status, Visibility status and owner state (PRD FR-75). `under_review` is a derived indicator while a Report is being reviewed, independent of the restricted flag; "lift restriction" and "restore removed" are distinct; removed Listings can move to archived on deactivation; "archive" is the only owner withdraw action; non-visible Listings return not-found. | CONFIRMED |
| B19 | One account-state model (Email-unverified, Active, Suspended, Deactivated) with a capability matrix, session behaviour and Listing-visibility effects (PRD FR-74). "Eligible owner" means an Active account. | CONFIRMED |
| B20 | Private-data egress to AI providers: phone, email and Private address are form-only; the assistant never asks for them; all user text is redacted before any provider call; assistant history stores redacted text; canary-based acceptance test; voice disclosure also for Visitors. | CONFIRMED |
| B21 | Administrator privilege model: a single Administrator role; reading private data (phone, Private address, Drafts, verification evidence, Report-linked Messages) requires a recorded reason and is audited; Report-linked Messages limited to the reported Message(s) plus limited context; no bulk export; audit scope extended to all admin writes and private-data reads. | CONFIRMED |
| B22 | Estimate lifecycle: the displayed AI Price Estimate is stored and bound to the published model-input snapshot; Visitors read it without triggering computation; edits to model inputs recompute it (or show it as unavailable, never stale); Draft and ad-hoc estimates are throttled per account. | CONFIRMED |
| B23 | Phone numbers are not required to be unique, and a duplicate number is never reported to the registrant (avoids an enumeration oracle). Quota evasion through extra accounts is accepted for the prototype and limited by email verification and abuse limits. | CONFIRMED |
| B24 | Quota correction window: within 24 hours of a counted Publication, the owner can correct locality and area once without archiving and without consuming a slot; Category, Subtype, purpose and city cannot be corrected this way. Refines B17. | CONFIRMED |
| B25 | Conversations accept Messages only while the Listing is publicly visible and neither participant is Deactivated (Suspended participants cannot send); otherwise read-only. The header shows the Listing title only while visible, else "Listing no longer available" (or Sold/Rented); the thread reopens if the Listing becomes visible again. | CONFIRMED |

## 2. Architecture decisions

| ID | Decision | Status |
|---|---|---|
| A1 | Django + Django REST Framework + PostgreSQL. | CONFIRMED |
| A2 | Django-managed authentication and authorization. Supabase Auth not used. Supabase may host plain PostgreSQL later. | CONFIRMED |
| A3 | Django Channels for real-time messaging with secure session handling. | APPROVED |
| A4 | One canonical property listing schema and a versioned attribute registry. | APPROVED |
| A5 | OpenAPI contract between frontend and backend. | APPROVED |
| A6 | Local development, testing and presentation. Production deployment decided later. | APPROVED |
| A7 | No task queue, Redis or other extra infrastructure initially. | APPROVED |
| A8 | ML price estimation and forecast run inside Django. No ML microservice. | CONFIRMED |
| A9 | LLM access through hosted APIs, preferring reliable free tiers (e.g. Gemini or alternatives). No locally hosted LLMs. Integration is provider-independent; specific models chosen later after testing availability, limits and structured-output quality. | CONFIRMED |
| A10 | Embeddings and speech-to-text: prefer hosted APIs or lightweight solutions; no unnecessary paid infrastructure. Provider choice deferred. | CONFIRMED (direction), DEFERRED (providers) |
| A11 | LLM output is untrusted. It never writes to the database or generates SQL. Validation uses the registry. | CONFIRMED (domain doc) |
| A12 | Database hosting: Docker PostgreSQL (with pgvector) for local development and automated tests; Supabase PostgreSQL as the hosted database (plain PostgreSQL only; Supabase Auth, Storage and other services remain unused, per A2). Django ORM and migrations are the only schema authority (no Supabase dashboard or SQL-editor schema changes). Connection settings are environment-specific with no hardcoded credentials. Supabase's PostgreSQL major version and pgvector availability must be checked and kept compatible with local and CI; the PostgreSQL 18 pin applies where Supabase allows, otherwise local and CI match the Supabase version. The migrator and runtime roles are created with SQL on Supabase; migrations use a direct connection, not the pooler. | CONFIRMED |

### Defaults carried forward (earlier recommendations, not objected to)

| ID | Default | Status |
|---|---|---|
| R1 | Session-cookie auth with CSRF. Next.js proxies `/api/*` to Django (same origin). Admin has its own login endpoint and an `IsAdmin` permission on all admin routes. | DEFAULT |
| R2 | Messaging: REST POST to send (idempotent via `client_message_id`), WebSocket receive-only. Server subscribes each socket only to a group derived from the authenticated account; recipients are read from the `Conversation` row; events published `on_commit`. History endpoints are participant-filtered. In-memory channel layer for the single-process local. | DEFAULT |
| R3 | Registry is a versioned YAML file in the backend repo, the single source for validation, serializers, AI extraction schema, allowed search filters, OpenAPI enums and a frontend-readable endpoint. Lower-value attributes marked `deferred`, not removed. | DEFAULT |
| R4 | Nearby facilities use the amenity catalog with `kind` ∈ {on_property, community, nearby} and `value_state`. Presence-only, no distance labels. | DEFAULT |
| R5 | Code-defined: fields, categories/subtypes, purposes, lifecycle states, area units. Database-managed: cities, localities, amenity/facility catalog, quota policies. ADM-045/047/048 become view-only. New `City` and `Locality` entities. | DEFAULT |
| R6 | `drf-spectacular`, committed schema with CI drift check, `/api/v1/`, one error envelope `{error:{code,message,fields,request_id}}`, `Idempotency-Key`, cursor pagination, separate public/private serializers. WebSocket events documented separately. | DEFAULT |
| R7 | Images via Django `STORAGES`: filesystem locally, S3-compatible later. Uploads pass through Django (type/size checks, Pillow verification, EXIF stripping). Random UUID keys. | DEFAULT |
| R8 | Quota defaults: calendar month, Asia/Karachi; republish in a new month counts; same-month republish and idempotent retry are free but recorded (`consumes_quota` flag); restoring a moderated listing is free; edits never consume quota. | DEFAULT |
| R9 | Email verification required before publishing or messaging; login, browse and favorites work without it. | DEFAULT |
| R10 | One generalized `Report` model (listing, conversation, message). Admins see message content only via a report. Phone-pattern check on listing title/description (not chat). | DEFAULT |
| R11 | Account deletion: admin-reviewed deactivation that anonymizes personal fields, archives listings, keeps messages with a "deleted user" sender. | DEFAULT |
| R12 | Private address stored in its own table with owner/admin-only access. Field-level encryption deferred to deployment-level disk/DB encryption. | DEFAULT |
| R13 | Valuation: sale estimates only in v1 (rent shows "unavailable"). Unsupported category or locality returns "unavailable". Failures logged in an `AIRequestLog` (no content). `ValuationResult.estimated_price` stays required. Forecast is a transparent low/base/high scenario model with a concise limitation note. | DEFAULT |
| R14 | Small items: rule-based duplicate flag (ADM-037), DB flag to disable AI features (ADM-055), small `SupportRequest` entity (USR-101), inquiry counts derived from conversations, optional view counter, TOTP admin 2FA as stretch. | DEFAULT |
| R15 | Shared agency account accepted. Audit records identify the account, not the employee. | DEFAULT |
| R16 | Implementation phases ordered by architectural dependency (P1 identity and platform foundation; P2 listings and publication; P3 discovery; P4 valuation; P5 AI capabilities; P6 messaging; P7 agency operations and governance; P8 integration, evaluation and polish). Phases sequence work for one developer; no requirement is removed or deferred for deadline reasons. Replaces the earlier T1/T2/T3 tiers. | DEFAULT |
| R17 | In-app notification list and local mail capture (development mail tool) for notifications and transactional email (PRD FR-76); email alerts for new Messages are not provided. | DEFAULT |

## 3. Requirement amendments to apply in the PRD

1. USR-023, USR-071, AGN-066: "forecast supported by historical data" becomes "5-Year Price Forecast based on documented synthetic assumptions", with a concise limitation note rather than repeated labels.
2. USR-017 and search rules: nearby facilities are seller-declared, never verified or distance-based.
3. SYS-011 and the privacy rule: city and locality are public, guests included (needed for search).
4. ADM-045, ADM-047, ADM-048: view-only (see R5).
5. ADM-001 to ADM-003: administrators are provisioned, never self-registered.
6. SYS-014 / quota rules: seed values 2 (individual) and 10 (agency), editable by administrators.

## 4. Requirements implied by the hosted-LLM decision

- Free-tier limits are not assumed to be unlimited or stable. The system degrades gracefully: provider error or rate limit never blocks manual listing creation or conventional search.
- Guest natural-language and voice search share the same free quota, so they need per-client throttling and a global budget guard, with fallback to conventional filters.
- Data minimization: never send phone, email or exact address to a provider. Send only the content needed for the task. Provider terms on how free-tier inputs may be used must be checked at selection time, and the UI should disclose that AI features use an external provider.
- Sample/seed data must bypass quotas (seed command, not the public publish path).
- Valuation and forecast cover Karachi localities present in the synthetic dataset. Anything else returns "unavailable".

## 5. Closed items from earlier findings

Resolved by confirmations above: forecast data basis (B13), agency scope (B3), admin UI (B3), nearby-facility data source (B8), stack inconsistencies (A1, A2, A8, B1), local-LLM hardware question (A9), quota values and supported city (B4, B5), phone requirement (B6), payments and language (B9, B10), scope and timeline (B11, B15).

## 6. Deferred to architecture (do not block the PRD)

Sale/rent terms storage (typed table vs registry-validated JSONB); whether a society is its own entity or a locality kind; draft-image access control; STT and embedding providers; specific LLM model(s); exact NFR thresholds and evaluation protocol (candidate metrics: MAE/MAPE/R² vs a baseline, labelled NL-search test set, scripted listing-assistant scenarios, authorization test matrix, concurrent-publish test).

## 7. Assumptions to confirm during the PRD workflow

- "Select or provide" nearby facilities: sellers choose from the facility catalog, and the AI assistant maps free-text mentions to catalog entries. Unmapped facility types are flagged, not silently stored.
- Nearby facilities are presence-only (no coarse distance labels).
- Phase order (R16) is a dependency-based proposal for a sole developer; the PRD workflow records it as DEFAULT, not as a scope decision.
