# EstateAI PRD Addendum

Companion to `prd.md`. This file holds detail that belongs to the **architecture phase** or does not earn a place in the PRD body. Nothing here changes a requirement. Status labels follow `docs/planning/decision-register.md`: **CONFIRMED**, **APPROVED**, **DEFAULT** (provisional, awaiting confirmation), **DEFERRED**.

## 1. Architecture handoff: mechanisms

| Topic | Direction | Status |
|---|---|---|
| Stack | Django, Django REST Framework, PostgreSQL backend; Next.js, React, Tailwind frontend including the custom admin area; two repositories | CONFIRMED |
| Authentication | Django-managed accounts and authorisation; Supabase Auth not used; Supabase may host plain PostgreSQL later | CONFIRMED |
| Real-time messaging | Django Channels with secure session handling | APPROVED |
| Session approach | Session cookie (HttpOnly, Secure, SameSite=Lax) with CSRF protection; Next.js proxies `/api/*` to Django so the browser sees one origin; separate Administrator sign-in endpoint and admin-only permission on every admin route | DEFAULT R1 |
| Chat design | Send over normal request (idempotent via client message id); WebSocket is receive-only; server derives recipients from the Conversation row, never from client input; publish after commit | DEFAULT R2 |
| Private-conversation enforcement | Handshake authenticates the session and rejects anonymous and Deactivated accounts and bad origins (Suspended accounts may connect to receive but cannot send, per FR-74); client never names a room, server subscribes the socket only to a group derived from the authenticated account; history endpoints are participant-filtered (non-participants get not-found); deactivation or sign-out closes the socket; tests cover participant, counterpart and intruder | DEFAULT R2 |
| Channel layer | In-memory for the single-process local environment; Redis or a Postgres-backed layer decided at deployment time | No Redis initially APPROVED (A7); in-memory layer DEFAULT R2 |
| Canonical schema | `PropertyListing` + `PropertyLocation` + `PrivatePropertyAddress` + `ResidentialDetail` / `PlotDetail` / `CommercialDetail` + `PropertyMedia` + `Amenity` / `ListingAmenity`; discard the older duplicate fields (city and locality text, private address and coordinates on the listing) | One canonical schema APPROVED (A4); entity decomposition DEFAULT (registry R3, address storage R12) |
| Field registry | One versioned YAML file in the backend repo drives validation, serializers, AI extraction schema, allowed search filters, API enums and a frontend-readable endpoint; lower-value attributes marked `deferred`, not removed | APPROVED (versioned registry); details DEFAULT R3 |
| Nearby facilities | Amenity catalogue with `kind` ∈ {on_property, community, nearby} and `value_state`; presence-only; no distance labels | DEFAULT R4 |
| Reference data split | Code-defined: fields, Categories/Subtypes, purposes, lifecycle states, area units. Database-managed: cities, localities, Amenity and facility catalogue, quota policies. New `City` and `Locality` entities | DEFAULT R5 |
| API contract | OpenAPI generated from the backend (`drf-spectacular`), committed schema with drift check, `/api/v1/`, one error envelope `{error:{code,message,fields,request_id}}`, `Idempotency-Key` on publish, cursor pagination, separate public and private serializers; WebSocket events documented separately | OpenAPI APPROVED; rest DEFAULT R6 |
| Image storage | Django `STORAGES`; filesystem locally, S3-compatible later; uploads pass through Django (type and size checks, image verification, metadata stripping, random keys); orphan cleanup by management command | DEFAULT R7 |
| Background work | No task queue, Redis or workers; AI calls in request path with timeouts; cleanup by management commands; revisit with a database-backed queue only if testing shows blocking | APPROVED |
| LLM | Hosted API, free tier preferred (for example Gemini or alternative); provider-independent interface; model chosen later by testing availability, limits and structured-output quality; output validated against the registry; no local LLM | CONFIRMED |
| Embeddings / RAG store | Hosted API or lightweight embedding; vector store in PostgreSQL (pgvector) or in-process fallback for a small knowledge base | DEFERRED |
| Speech-to-text | Hosted API or lightweight server-side option (browser-native recognition excluded, FR-32); transcript always shown for correction; audio not retained | DEFERRED |
| Valuation | Inference inside Django; synthetic Karachi dataset with documented generator assumptions; gradient-boosted or random-forest regression per proposal; forecast as a transparent low/base/high scenario model, not a trained forecaster; sale only in v1; failures logged to an AI request log, not as valuation rows | ML in Django CONFIRMED; presentation and terminology CONFIRMED (B13); rest DEFAULT R13 |
| Publication transaction | Row-locked, idempotent publish and quota accounting in one database transaction; external calls (AI, uploads, email) outside it; `consumes_quota` flag so free same-month republication is still recorded | Transaction rules per Domain doc; flag DEFAULT R8 |
| Edit concurrency | Optimistic concurrency via listing revision; accepted changes recorded with origin (manual or AI) | Domain doc; PRD FR-22 |
| Search safety | Model emits validated filters only; backend builds queries through the ORM; no model-generated SQL; guest AI throttling and global budget guard | CONFIRMED (no LLM SQL); guards per Register §4 |
| Data model additions to carry | `City`, `Locality`, `Report` (generalised: listing, conversation, message), `AIRequestLog`, feature-disable flag, `SupportRequest`, `consumes_quota` | DEFAULT |

## 2. Rejected or deprioritised alternatives (rationale)

- **Supabase Realtime for chat (proposal diagram).** Would require minting tokens and duplicating participant rules as database row-level policies, creating two sources of authorisation truth, and would require the Supabase stack locally. Direction chosen instead: Django Channels. The proposal's rationale (not flooding Django with chat traffic) does not apply at the local scale. *Deviation from the approved proposal diagram; confirmed direction per register (Django auth, Channels).*
- **Locally hosted LLMs.** Rejected by the team (hardware limits and Roman Urdu extraction quality).
- **Celery / Redis / task queue.** Not needed for the initial version; no workload requires it.
- **PostGIS, Places APIs, distance search.** Rejected for v1; nearby facilities are seller-declared.
- **Separate ML microservice.** Rejected; inference inside Django.
- **Elasticsearch / separate vector search for listings.** Not needed; structured PostgreSQL search first.
- **Tokens in browser storage for authentication.** Less safe than session cookies for this setup.

## 3. Evaluation protocol (supports SM-2 to SM-11)

- **Valuation:** train and evaluate on a synthetic Karachi dataset with a held-out split; report MAE, MAPE and R² against a median-price-per-area-by-locality baseline; state that the numbers describe the synthetic generator, not the real market. Keep the generator's assumptions in a document readers can inspect.
- **Natural-language search:** a labelled set of 100+ queries (at least 60 English, plus Roman Urdu and Urdu), including negative cases (unsupported criteria, missing data); report extraction F1 per language and the share of model outputs rejected or repaired by validation; measured against live providers (the recorded stub is for regression and SM-1 determinism only).
- **AI Listing Assistant:** 30 scripted seller scenarios, including corrections, contradictions and missing data; record required-field accuracy and count invented values (target zero reaching a Published listing).
- **RAG:** 40 domain questions; hit at top 3 for at least 32 (80%), measured with the project's local embedding model.
- **Voice:** report word error rate, no threshold.
- **Authorisation:** an endpoint-by-endpoint matrix of Visitor, owner, other account, Administrator.
- **Concurrency:** parallel Publication requests against one remaining slot; retries with the same idempotency key.
- **Resilience:** fault injection that disables AI and retrieval.
- **Privacy:** automated scan for seeded canary phone numbers and Private addresses in all non-owner responses; canary phone, email and address values that conform to the shared patterns must not appear in recorded AI-provider requests; a false-positive set confirms that prices, areas and counts are preserved; a table-driven test covers the FR-74/FR-75 matrix.
- **Usability:** SUS with at least 8 participants.

## 4. Amendments to source wording (all need confirmation)

| Source | Amendment |
|---|---|
| USR-023, USR-071, AGN-066 | "Forecast supported by historical data" → 5-Year Price Forecast on documented synthetic assumptions, with a concise limitation note (no repeated demo labels) |
| USR-017 | Nearby facilities are seller-declared, never verified |
| SYS-011, privacy rule | City and locality public to Visitors as well as authenticated users |
| ADM-045, ADM-047, ADM-048 | View-only for Administrators (DEFAULT R5) |
| ADM-001–003 | Administrator accounts provisioned only, no registration |
| SYS-004 | Restates the source (employees share one account); the "no seats" stance is DEFAULT R15 |
| USR-042, SYS-013, AGN-031, USR-087, USR-088, AGN-069, AGN-070 | Verified-email precondition for publishing and messaging (DEFAULT R9) |
| USR-077, AGN-035 | Identity fields locked while a Listing is published, with a one-time correction (B17, B24; CONFIRMED) |

## 5. Source coverage

All 282 source requirement IDs (USR-001–101, AGN-001–082, ADM-001–064, SYS-001–035) appear in the `Source:` lines of FR-1 to FR-80; the consolidation merges near-duplicate user and agency requirements, which share one underlying capability. Domain-document rules not carried as separate source IDs (concurrency, retention, search semantics, AI contracts) are cited as "Domain §…" in the relevant FRs.

## 5a. Technical documentation required for valuation and forecast (supports FR-47)

The user-facing interface stays clean (terms: AI Price Estimate, Estimated Property Value, 5-Year Price Forecast; one unobtrusive info affordance, no repeated banners). The following are documented in the technical documentation instead:
- Synthetic dataset: generator design, per-locality base rates and multipliers, noise model, assumptions and known unrealism; coverage (Karachi localities, Categories).
- Model: algorithm choice, features (Private address, phone and Asking price excluded), feature-schema and model versions, training and evaluation procedure.
- Evaluation: held-out split, MAE, MAPE and R² against a median-price-per-area baseline; statement that results describe the synthetic generator, not the real market.
- Forecast: scenario parameters (low, base, high growth), how they vary by locality or category, and limits of the method.
- Limitations and claims policy: not a verified valuation, not a guaranteed return, academic prototype.
- Content of the short "About AI Price Estimates" page linked from the info affordance.

## 5b. Delivery approach (planning input, no scope effect)

Sole developer with Claude Code; small, testable, sequential tasks; phases P1 to P8 in PRD §6.3 are dependency-ordered **[DEFAULT R16]**. No requirement is deferred for deadline reasons. Frontend and backend repositories are planned together; each phase includes both.

## 5c. Data and architecture implications of the validation decisions (B17-B22)

- **Quota identity (B17):** persist an identity-field fingerprint (Category, Subtype, purpose, city, locality, area with unit) with each counted Publication event; same-month republication is free only on a matching fingerprint. Identity fields become read-only while a Listing is published.
- **Lifecycle and visibility (B18):** implement FR-75's table as one policy function used by discovery queries, detail endpoints, favourites, media access and WebSocket events; not-found is the single response for non-visible Listings. Add `removed -> archived` to the transition rules.
- **Account state (B19):** model the four states with a capability matrix enforced in one place; derive Listing eligibility from owner state at query time (suspension) and mutate Listing status only on deactivation.
- **AI egress (B20):** a mandatory redaction layer in front of every provider call (assistant, search, transcript); assistant history stores redacted text; canary-based test harness against a recorded provider stub, which also gives SM-1 a deterministic provider mode.
- **Administrator access (B21):** a reason-required access path for private reads, written to the audit log in the same transaction; audit capture exists from the first phase that writes admin actions; Report-linked message view limited to the reported Message(s) plus 5 preceding.
- **Estimate (B22):** store the estimate per published revision with the input snapshot; recompute on model-input edits; ad-hoc and Draft estimates throttled per account; Visitors never trigger computation.

## 5d. Architecture cautions to resolve before promising SM-7 and the latency budget

- Next.js `/api` rewrites do not proxy WebSockets, so the real-time connection needs its own origin and Origin-header handling; cookie `Secure` behaviour on plain local HTTP differs by browser.
- A single process running AI calls (up to 15 s), image processing and WebSocket delivery can starve chat; keep AI and image work off the WebSocket path or bound it.
- Registry changes (FR-22) need a versioning and revalidation strategy for Drafts and Assistant sessions.
- Notifications (FR-76), retention (§8.3), abuse limits (NFR-10) and the Report/evidence snapshot add entities: `Notification`, report evidence snapshot, view counter, `SupportRequest`, retention jobs (management commands, no queue).

## 5e. Decisions added after round-2 validation (B23-B25) and structural fixes

- **Visibility (B18, refined):** model `restricted` as an independent flag; `under review` is derived from the existence of a Report in reviewing, never stored as a visibility value. Public predicate: status = published AND not restricted AND owner Active.
- **Account state (B19, refined):** keep email verification, suspension and deactivation as separate conditions; reactivation returns to the prior email-verification condition.
- **Quota correction (B24):** a one-time locality/area correction within 24 hours of the counted Publication updates the identity-field fingerprint without a new slot; any further identity change follows B17.
- **Phone (B23):** no uniqueness constraint; no duplicate-phone response; quota evasion through extra accounts is accepted for the prototype.
- **Conversations (B25):** thread writability is derived from the Listing's public visibility and the participants' account states; the header title is derived the same way.
- **Estimate (B22, refined):** bind the stored estimate to the model-input snapshot, not the revision; owner-triggered recompute covers failures and new model versions.
- **Reports:** snapshots hold public-view Listing content only and the selected Message(s) plus 5 preceding; viewing Message snapshots is a reasoned, audited read.

## 6. Deferred to architecture (do not block the PRD)

Sale and rent terms storage (typed table or registry-validated flexible field); whether a society is its own entity or a locality kind; draft-image access control; STT and embedding providers; specific LLM model(s); field-level encryption (deployment-level encryption assumed, DEFAULT R12); exact NFR thresholds after the first evaluation run; deployment target.

## 7. Product-level design notes retained from the Domain document

- Unknown is never zero or false; an explicitly absent amenity differs from an unanswered one.
- Plot legal, approval and possession attributes are always "seller-declared" unless independently verified; no EstateAI badge for them.
- Warehouses, factories and commercial buildings are a documented future extension, not v1.
- AI extraction states (known, unknown, explicitly none, not applicable, conflict, declined, stale) describe behaviour; they do not require a column each.
- AI listing history and revisions must not store unrestricted copies of private data; retention periods are set in PRD §8.3 and implemented by management commands.

## 8. Listing attribute catalogue (from Domain §2.4; supports FR-12)

R = required for Publication, Rec = recommended, O = optional, C = conditional. **Deferred** fields stay in scope and are added as registry-driven extensions in P8 at the latest **[DEFAULT R3]**: payment type, down payment, installment fields, title/legal/approval/mortgage declarations, video or virtual tour, floor plan.

| Group | R | Rec | O / C |
|---|---|---|---|
| Common (all Listings) | purpose, Category, Subtype, title, Asking price, price basis (rental period when rent), city, locality, area, area unit, at least one image | description, society or scheme, phase or sector, block | price negotiable, covered area, orientation, corner, park-facing, private street address (restricted); C: marla conversion basis (225, 250 or 272.25 sq ft); deferred: video or virtual tour, floor plan |
| House | bedrooms, bathrooms | total floors, kitchens, construction year, condition, furnishing, parking spaces, occupancy status, covered area, construction status | drawing rooms, dining rooms, TV lounges, study rooms, prayer rooms, store rooms, laundry rooms, powder rooms, servant quarters, basement, plot dimensions |
| Apartment | bedrooms, bathrooms | floor number, total building floors, covered or unit area, furnishing, condition, parking spaces, lift, backup electricity, building security, occupancy status | construction year, maintenance charges, balcony or terrace, building lobby, intercom |
| Plot (Residential Plot, Commercial Plot, Plot File) | plot Subtype, plot area | plot dimensions (width, depth), possession status, corner plot, road width, balloting status, electricity, water, sewerage, development status, approval authority | park-facing, main boulevard, orientation, gas, boundary wall; C: plot file status; deferred: approval or reference number, title or document type, mortgage or dispute declaration, installment availability, down payment, remaining installments |
| Commercial (Shop, Office) | commercial Subtype, area | intended or permitted business use, floor number, frontage (shops), condition, furnishing or fit-out, parking spaces, electricity, lift (offices), security, road accessibility, occupancy status | total building floors, ceiling height, available power load, three-phase electricity, water and gas, building lobby, conference rooms, business centre facilities, maintenance charges |
| Sale terms | total Asking price | possession availability | price negotiable; deferred: payment type, down payment, total installment price, monthly installment, remaining installments, ownership or title document type, approval information |
| Rent terms | rental amount, rental period (day, week, month or year; never assumed monthly) | security deposit, availability date, furnishing status | advance rent, minimum lease duration, notice period, maintenance charges, included utilities, pets allowed, occupant restrictions |

**Amenity vocabulary (stable codes):** utilities (electricity backup, broadband internet, waste disposal); building (double-glazed windows, central air conditioning, central heating, elevator, lobby, intercom); residential community (community lawn, swimming pool, gym, daycare, children's play area, BBQ area); community services (community mosque, medical facility, community centre); business (conference room, business centre, ATM in building). **Seller-declared nearby facilities:** mosque, park, gym, school, hospital (catalogue extension per OQ-1). Plot legal, approval and possession attributes are always seller-declared and never receive an EstateAI badge.
