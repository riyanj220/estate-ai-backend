---
title: EstateAI
status: final
created: 2026-11-18
updated: 2026-11-18
---

# PRD: EstateAI
*Working title: "EstateAI: An Intelligent Property Marketplace Featuring Conversational NLP Search, RAG-Powered Listings, and Dynamic ML Valuation" (approved FYP title).*

## 0. Document Purpose

This PRD defines the **whole** EstateAI product (web frontend, backend, data and AI behaviour) for the project team, the FYP supervisors and the downstream BMAD workflows (architecture, UX, epics and stories). It is written at product level: capabilities, journeys, acceptance conditions, quality bars and success measures. Technical mechanisms live in `addendum.md` and the later architecture document. This repository holds only the backend, but requirements span both repositories.

**Inputs this PRD builds on (all authoritative, none duplicated here):** the approved FYP Proposal (authority for the four committed features); *Actors and Functional Requirements* (source IDs `USR-`, `AGN-`, `ADM-`, `SYS-`); *Domain Modeling & Database Architecture*; and `docs/planning/decision-register.md` (CONFIRMED / APPROVED / DEFAULT decisions). Where the register and older documents disagree, the register wins.

**How to read it.** Vocabulary is anchored in the Glossary (§3). Features (§4) nest globally numbered FRs (FR-1 … FR-80), each with testable consequences and a `Source:` line naming the original requirement IDs it covers, so no approved requirement is silently dropped. Every FR carries an **implementation phase** **[P1]–[P8]**, ordered by architectural dependency for a sole developer (see §6). Phases sequence work; they never remove or defer scope for deadline reasons. Decisions the team has only provisionally accepted are tagged **[DEFAULT Rn]** (see §10) and are not treated as confirmed. Inferences are tagged `[ASSUMPTION: …]` and indexed in §11. Other tags: `[B17]`–`[B25]` mark confirmed validation decisions (register); `[OQ-n]` an open question (§9); `[NON-GOAL]` an explicit exclusion; `[NOTE FOR PM]` a flagged tension; `[BE]`, `[FE]` and `[BE+FE]` the repository that owns the FR.

---

## 1. Vision

Pakistani property portals are largely filter-driven directories. Buyers fight drop-down filters, sellers hand-write generic adverts and guess prices, and the two sides are pushed onto private phone calls where leads and trust leak away. EstateAI is a property marketplace for **sale and rent** (houses, apartments, plots and commercial units), launching in **Karachi**, that replaces those frictions with four connected capabilities: a conversational **AI Listing Assistant** that interviews a seller and builds a complete Listing; an **AI Price Estimate and 5-Year Price Forecast** for sale Listings that give both sides an objective reference; **natural-language text and voice search** that understands "a 3-bedroom flat in Gulshan with a masjid and park nearby"; and **in-platform real-time chat** that keeps phone numbers private and conversations on the platform.

**Thesis.** AI is useful here only if it makes the marketplace *more trustworthy and faster*, never if it makes it less accurate. Every AI feature therefore consumes or updates the same validated Listing data; the AI never invents facts, never publishes without the seller's approval, and its failure never blocks a human from listing or searching. Estimates are presented as AI-generated approximations, never as verified valuations or guaranteed returns.

**Why it matters for the FYP.** EstateAI is a university Final Year Project. Success means a complete, polished, well-engineered application that exercises all four committed features end to end, with measurable evidence of correctness and documented limits. EstateAI is an academic prototype, not a commercial real estate service.

---

## 2. Target User

### 2.1 Jobs To Be Done
- **Buyer or renter (Individual or Agency account, or Visitor):** "Let me describe what I want in my own words and see matching Listings"; "show me how the asking price compares with an AI estimate"; "let me contact the seller without giving out or receiving a phone number."
- **Seller (Individual account):** "Help me create a good Listing without writing it myself"; "tell me a realistic price before I publish"; "let me manage and archive my Listing easily."
- **Agency:** "Give my business its own identity, dashboard and higher publishing allowance"; "let customers trust us (optional verification)"; "keep all inquiries in one place."
- **Administrator:** "Keep the marketplace trustworthy: act on reports, verify agencies, adjust publishing limits, and leave an audit trail."
- **Developer (FYP):** "Deliver all four committed features, integrated and polished, with honest evaluation and documented synthetic data and limitations."

### 2.2 Non-Users (v1)
- Investors who need validated, real-market forecasts (the 5-Year Price Forecast is an academic-prototype output, not a validated prediction).
- Users or properties outside Karachi (structure is extensible; supply is Karachi only).
- Users who need a non-English interface.
- Agency employees who need their own seats (employees share the Agency account in v1) **[DEFAULT R15]**.
- Anyone needing payments, subscriptions or escrow (out of scope).

### 2.3 Key User Journeys

- **UJ-1. Hira describes the home she wants and judges the price.**
  - **Persona + context:** Hira Qureshi, a Karachi schoolteacher moving closer to work, wants a 3-bedroom flat near a masjid and park and distrusts price claims.
  - **Entry state:** Visitor (not signed in), web, desktop.
  - **Path:** Opens EstateAI; types "3 bedroom apartment in Gulshan-e-Iqbal under 2 crore with a masjid and park nearby"; sees the interpreted filters (apartment, 3+ bedrooms, locality, price ≤ 2 crore, nearby masjid and park labelled *seller-declared*); scans results sorted by relevance; opens one Listing; sees location, amenities and the Asking price beside the **Estimated Property Value** and the **5-Year Price Forecast**; saves it (prompted to register).
  - **Climax:** The Listing's Asking price shows "12% above the Estimated Property Value"; a small info icon notes it is an AI-generated estimate, and she knows to negotiate.
  - **Resolution:** Registered, favourite saved, ready to message the seller (UJ-4).
  - **Edge case:** Her query includes "near a metro station", which is not supported; the system states the unsupported criterion and runs the rest instead of silently ignoring it.
  - *Realizes:* FR-1, FR-4, FR-33, FR-35, FR-36, FR-39, FR-40, FR-44, FR-45, FR-47.

- **UJ-2. Faisal searches by voice on his phone.**
  - **Persona + context:** Faisal Sheikh, a shop owner searching between customers on a phone browser.
  - **Entry state:** Visitor, mobile web.
  - **Path:** Taps the microphone; says "four bedroom house in DHA Karachi under three crore"; sees the transcript and the interpreted filters; corrects one word; confirms; reads results.
  - **Climax:** He gets matching houses without typing a filter.
  - **Resolution:** Opens a Listing from the results.
  - **Edge case:** Microphone permission is denied or transcription fails; the system tells him and offers the text box with nothing lost. A Roman Urdu variant ("DHA Karachi mein 3 crore se kam ka 4 bedroom ghar") is tried in evaluation and its quality reported (FR-37).
  - *Realizes:* FR-37, FR-36, FR-33.

- **UJ-3. Bilal lists his house through the AI Listing Assistant.**
  - **Persona + context:** Bilal Ahmed, a first-time seller of a 240-square-yard house in DHA Phase 6, hates paperwork.
  - **Entry state:** Signed in with an Individual account, email verified, one publication remaining this month.
  - **Path:** Chooses "Create with AI"; types "I want to sell my 240-yard double-storey house in DHA Phase 6, five bedrooms, six bathrooms, parking for two cars, a masjid nearby, asking 6 crore"; the AI Listing Assistant fills category, locality, area, bedrooms, bathrooms, parking, nearby masjid and price, then asks only what is missing (title, images, condition); he uploads photos; he sees the **AI Price Estimate** as a baseline; reviews the generated description; edits one sentence manually; approves; publishes.
  - **Climax:** The Listing goes live immediately and his dashboard shows "0 publications remaining this month."
  - **Resolution:** Listing visible to Visitors; Bilal waits for inquiries.
  - **Edge case:** Mid-conversation he says "four bedrooms" then "actually five"; the draft holds five. Separately, if the AI provider is rate-limited, he sees a notice and continues in the manual form with everything intact.
  - *Realizes:* FR-24 – FR-32, FR-11 – FR-18, FR-43.

- **UJ-4. Hira messages the seller; Bilal replies.**
  - **Persona + context:** Continues UJ-1 and UJ-3 from both sides.
  - **Entry state:** Both signed in, emails verified.
  - **Path:** Hira opens Bilal's Listing and sends an inquiry; Bilal sees an unread Conversation, opens it and replies in real time; the Conversation header names the Listing.
  - **Climax:** Hira receives the reply instantly while on the page, with no phone number exchanged or shown.
  - **Resolution:** Conversation history persists, including after the Listing is later marked sold.
  - **Edge case:** A third user guesses the Conversation URL and gets no access; Bilal cannot message his own Listing as an inquiry.
  - *Realizes:* FR-48 – FR-53.

- **UJ-5. Sana runs an agency on EstateAI.**
  - **Persona + context:** Sana Mirza owns Bayview Estates, a small Karachi agency; her two employees share the Agency account.
  - **Entry state:** Visitor.
  - **Path:** Registers through the Agency form (agency name, business email, business phone, office address); verifies email; completes the profile; creates Listings manually and with the AI Listing Assistant; submits an optional verification request; monitors the dashboard (listing counts by status, publication quota, inquiries).
  - **Climax:** On the 11th publication in a month she is told the Agency quota (10) is exhausted and when it resets; her already-published Listings are unaffected.
  - **Resolution:** An administrator approves verification; the verified badge appears on her Listings and public Agency profile.
  - **Edge case:** If verification is rejected she sees the reason and may resubmit.
  - *Realizes:* FR-2, FR-4, FR-7, FR-11 – FR-18, FR-24 – FR-32, FR-55, FR-57, FR-58, FR-62.

- **UJ-6. Imran moderates the marketplace.**
  - **Persona + context:** Imran Ali, a platform administrator.
  - **Entry state:** Administrator account (provisioned, not self-registered) in the protected admin area.
  - **Path:** Reviews a Report that a Listing's price is misleading; reads the reason and the Listing; restricts the Listing; records the reason; changes the Individual publication quota from 2 to 3; reviews an Agency verification request.
  - **Climax:** The restricted Listing disappears from public discovery, the owner is informed, and an Audit record captures who did what and why.
  - **Resolution:** The Report is marked resolved; a later restoration remains possible.
  - **Edge case:** A non-administrator attempts the same admin action and is denied.
  - *Realizes:* FR-9, FR-59, FR-60, FR-62, FR-63, FR-64, FR-65, FR-69, FR-76.

---

## 3. Glossary
*Used verbatim throughout; no synonyms.*

- **Account** — A registered identity. Exactly one type: **Individual account**, **Agency account** or **Administrator account**.
- **Individual account** — One account for both buying and selling. No role selection.
- **Agency account** — A business account with extra registration details, its own dashboard, a higher Publication quota and optional Verification. It represents the organisation; employees share it in v1.
- **Administrator account** — Privileged account, provisioned by the platform only; uses the protected admin area.
- **Visitor** — Someone not signed in. May browse, search and view; cannot save favourites, publish or message.
- **Buyer / Seller** — Activities of an Individual or Agency account, not account types.
- **Listing** — A property advertisement for **sale** or **rent**, owned by exactly one Individual or Agency account. Manual and AI-assisted creation use the same Listing.
- **Draft** — A Listing not yet published; may be incomplete.
- **Published listing** — A Listing that is published, not restricted and whose owner is eligible; the only kind publicly discoverable.
- **Listing status** — draft, published, archived, sold, rented, removed.
- **Visibility status** — normal or restricted; independent of Listing status. *Restricted*: set only by an Administrator; the Listing is hidden. A Listing also shows as **under review** (a derived indicator) while at least one Report on it is in reviewing; this never changes visibility or owner rights.
- **Account state** — Email-unverified, Active, Suspended or Deactivated (FR-74). Not to be confused with an Agency's Verification status.
- **Owner** — The Individual or Agency account that owns a Listing; the "creator" of a Draft is its owner.
- **Eligible owner** — An owner whose account is in the Active state.
- **Identity fields** — A Listing's Category, Subtype, purpose, city, locality and area with unit; they define which property a Listing advertises (FR-18, FR-75). Locality and area can be corrected once within 24 hours of a Publication (B24).
- **Category / Subtype** — Category: House, Apartment, Plot, Commercial. Subtypes: Residential Plot, Commercial Plot, Plot File, Shop, Office (plus House and Apartment as their own).
- **Asking price** — The Seller's price. Never overwritten by an estimate.
- **AI Price Estimate** — The ML-generated price estimate for a Listing; its headline figure is shown as the **Estimated Property Value**. Never a verified market valuation.
- **5-Year Price Forecast** — The five-year appreciation forecast shown with a low, base and high range **[DEFAULT R13]**, based on documented synthetic assumptions. Never a guaranteed return or validated prediction.
- **Public location** — City and locality, visible to everyone including Visitors.
- **Private address** — Exact address; visible only to the owner and authorised Administrators.
- **Amenity** — A standard feature of the property or its community (for example elevator, backup electricity).
- **Seller-declared nearby facility** — A facility such as a mosque, park, gym, school or hospital that the Seller states is nearby. Presence-only; never geographically verified; always labelled seller-declared.
- **AI Listing Assistant** — The conversational assistant that collects Listing information, proposes updates and drafts a description.
- **Assistant session** — One saved conversation between an account and the AI Listing Assistant about one Draft. A Draft may have several over its life; one is active at a time.
- **Field registry** — The single versioned definition of Listing fields (names, types, requirement levels, allowed values) shared by forms, AI, search and validation.
- **Natural-language search / Voice search** — Free-text or spoken search interpreted into structured filters. Voice search transcribes first, then follows the same path.
- **Search interpretation** — The structured filters the system derived from a query, shown to the user.
- **Publication** — The act of making a Draft or archived Listing a Published listing. **Republication** — publishing an archived Listing again.
- **Publication quota** — The monthly limit of quota-consuming Publications per account type (individual 2, agency 10; configurable).
- **Conversation** — A private two-account message thread about one Listing. **Inquiry** — the first Message that opens a Conversation. **Message** — one text message. **Participant** — one of the two accounts in a Conversation.
- **Report** — A user submission about a Listing, Conversation or Message. **Moderation action** — an Administrator decision (restrict, remove, restore, dismiss, resolve).
- **Verification** — An optional Administrator-granted status for an Agency account.
- **Audit record** — An immutable record of a security-sensitive or policy action.
- **Support request** — A message from an Individual or Agency account to the platform team (FR-71).
- **Notification** — An in-app message about an event that affects an account (FR-76).
- **Favourite** — A Published listing saved by an account (FR-40).
- **Account holder** — The person using an account; the account itself is the party that owns Listings and takes part in Conversations.
- **Implementation phase** — P1 to P8, a dependency-ordered group of FRs (see §6).

---

## 4. Features

### 4.1 Accounts, Registration & Authentication
**Description:** Three Account types share one sign-in experience. Individual accounts register with a short form; Agency accounts register through a dedicated business form with extra details; Administrator accounts exist only because the platform provisioned them. Realizes UJ-3, UJ-5, UJ-6.

#### FR-1: Individual registration [P1] [BE+FE]
A Visitor can register an Individual account with full name, email address, password and phone number, all mandatory.
Source: USR-001, USR-010, SYS-001, SYS-002.
**Consequences (testable):**
- Registration is rejected with field-level messages when any field is missing or invalid; email uniqueness is case-insensitive and enforced, but a duplicate email is not disclosed in the response, which is identical for new and existing addresses (the address receives a verification message or an "already registered" notice) `[ASSUMPTION]`.
- **[B23]** Phone numbers are not required to be unique, and a duplicate number is never reported to the registrant.
- Phone number is validated for format `[ASSUMPTION: Pakistani mobile or valid international format]` and is never shown to any other account or Visitor.
- The resulting account can both create Listings and start Inquiries without choosing a role.
- Password meets a minimum policy `[ASSUMPTION: at least 8 characters, not a commonly used password]`.

#### FR-2: Agency registration [P1] [BE+FE]
A Visitor can register an Agency account through a dedicated business form collecting agency name, business email, business contact number, office address and city (all mandatory) and sign-in credentials.
Source: AGN-001–005, SYS-003, SYS-004.
**Consequences (testable):**
- The Agency form is distinct from the Individual form and creates an account of Agency type whose Agency Verification status is "unverified" (FR-57).
- Business phone is private, like FR-1.
- The Agency's city and locality are public on its profile; its full office address is private `[ASSUMPTION]`.
- Several employees may use the one Agency account; the system does not distinguish them `[NON-GOAL: employee seats]` **[DEFAULT R15]**.

#### FR-3: Sign-in and sign-out [P1] [BE+FE]
An account holder can sign in with registered credentials and sign out securely.
Source: USR-002, USR-003, AGN-006, AGN-007.
**Consequences (testable):**
- Wrong credentials produce a generic error that does not reveal whether the email exists.
- After sign-out, authenticated actions are rejected until signing in again.
- Repeated failed sign-ins are throttled `[ASSUMPTION: 5 failures per 15 minutes per account and address]`.
- Suspended accounts can sign in with restricted capabilities; Deactivated accounts cannot sign in (FR-74).
- A new session is issued at every sign-in; sessions end on sign-out, password change (FR-5) and deactivation (FR-74).
- A throttled sign-in returns the same generic message and clears automatically when the window ends.

#### FR-4: Email verification [P1] [BE+FE]
An account holder can verify their email address from a link sent to it.
Source: USR-004, AGN-008.
**Consequences (testable):**
- Verification links are single-use and expire `[ASSUMPTION: 24 hours]`; a new link can be requested.
- **[DEFAULT R9]** Until verified, an account can sign in, browse and save favourites but cannot publish Listings or send Messages; the UI says why.

#### FR-5: Password reset and change [P1] [BE+FE]
An account holder can reset a forgotten password by email and change it while signed in.
Source: USR-005, USR-006, AGN-009.
**Consequences (testable):**
- Reset links are single-use, expire after 1 hour `[ASSUMPTION]` and do not reveal whether an email is registered.
- Changing the password ends other active sessions `[ASSUMPTION]`.

#### FR-6: Individual profile [P1] [BE+FE]
An Individual account holder can view and update profile information (name, phone) and upload or change a profile picture.
Source: USR-007, USR-008.
**Consequences (testable):**
- Phone remains mandatory after edits and remains private.
- Changing the email address requires verifying the new address before it replaces the old one; the old address stays valid until then `[ASSUMPTION]`.
- Pictures follow the same file rules as FR-15.

#### FR-7: Agency business profile [P1] [BE+FE]
An Agency account holder can view and update its business profile: name, contact details, address, description and logo.
Source: AGN-010–013.
**Consequences (testable):**
- Profile edits appear on the public Agency profile (FR-41, P3) except private fields (business phone, full office address).
- Business profile data is editable only by the Agency account; the dashboard that consumes it is FR-55 (P7).
- Agency name and description are subject to the contact-detail check of FR-23.

#### FR-8: Account deactivation or deletion request [P7] [BE+FE] **[DEFAULT R11]**
An account holder can request deactivation or deletion of their account.
Source: USR-009, AGN-014.
**Consequences (testable):**
- The request is recorded and visible to Administrators (FR-61); the account remains active until decided.
- **[DEFAULT R11]** An approved request deactivates the account, anonymises personal fields, archives its Listings (including any removed ones, FR-75) and retains Messages with a "deleted user" sender. Hard deletion never cascades into quota, moderation or audit history.
- An Administrator approves or rejects each request with a recorded reason (FR-61, FR-69); a rejected request leaves the account Active.
- On approval, Drafts are retained but inaccessible, and sold or rented Listings are archived by the system (FR-75).
- Quota history does not carry over to a later registration with the same email or phone.

#### FR-9: Administrator accounts and protected admin area [P1] [BE+FE]
Administrator accounts are provisioned by the platform; there is no public Administrator registration. Administrators sign in to a protected admin area and can reset or change their password.
Source: ADM-001–004, SYS-001.
**Consequences (testable):**
- No registration path creates an Administrator account.
- Non-Administrator accounts and Visitors receive an access-denied result for every admin capability, enforced by the backend regardless of what the frontend shows.
- Administrators cannot own Listings.
- **[B21]** There is a single Administrator role. Reading a phone number, Private address, Draft, Assistant session history, Verification evidence, support request content or Report-linked Messages requires entering a reason; each such read is recorded (FR-69). No bulk export of private data exists.
- Administrators can sign out securely (ADM-002); an Administrator session expires after 30 minutes idle and 8 hours in total `[ASSUMPTION]`.
- Failed Administrator sign-ins are throttled and recorded (FR-69), and passwords follow a strong policy.
- Provisioning uses a platform-side mechanism with no default credentials and is recorded (FR-69). A forgotten Administrator password is reset through the same mechanism rather than by self-service email `[ASSUMPTION]`; Administrators can change their password while signed in.
- Administrator accounts can be disabled or removed through the same mechanism, recorded in the audit trail. Reasoned reads are a detective control: they are logged and reviewable (FR-69), not blocked in advance.

#### FR-10: Strengthened Administrator authentication [P7] [BE+FE] **[DEFAULT R14]**
Administrator sign-in supports a second factor (time-based one-time code), in addition to the baseline controls in FR-9.
Source: ADM-005.
**Consequences (testable):**
- An Administrator can enrol a second factor; once enrolled, sign-in fails without a valid code.
- Second-factor enrolment and use are recorded (FR-69).
- Until this FR is delivered (P7), the baseline controls of FR-9 apply.

#### FR-74: Account states and capabilities [P1] [BE+FE]
Every Individual and Agency account is in exactly one **Account state**, which determines what it can do, how its sessions behave and whether its Listings are discoverable. **[B19]**
Source: SYS-030, ADM-010, ADM-012, ADM-023; Domain §2.5.
**Consequences (testable):**

| State | Entered by | Sign in | Browse, search, view favourites | Create and edit Drafts | Publish, republish | Send Messages | Read own Conversations | Report, support request, deactivation request | Its Listings publicly visible |
|---|---|---|---|---|---|---|---|---|---|
| Email-unverified | Registration | Yes | Yes | Yes | No **[DEFAULT R9]** | No **[DEFAULT R9]** | Yes | Yes | n/a (cannot publish) |
| Active | Email verified (FR-4) | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Suspended | Administrator (FR-59) | Yes, restricted | Yes | Read only | No | No | Yes, read only | Support and deactivation requests only | No (derived; returns on reactivation) |
| Deactivated | Approved deactivation (FR-8) | No | Visitor level | No | No | No | No | No | No (Listings archived) |

- **Eligible owner** means an account in the Active state. Listings of any other owner state are not publicly discoverable.
- Suspension never changes Listing status; reactivation restores previous visibility. Deactivation archives Listings (FR-8); reactivation of a Deactivated account is not supported `[ASSUMPTION]`. Suspension and deactivation are applied over the email-verification condition: reactivating a Suspended account returns it to Active if its email is verified, otherwise to Email-unverified.
- Suspension keeps sessions valid but every restricted action is refused on the next request; deactivation ends all sessions and real-time connections immediately.
- The reason for suspension is shown to the account at sign-in.
- Capabilities that depend on later FRs (support and deactivation requests, Reports) become available as those FRs are delivered.

#### FR-76: Notifications and email delivery [P1] [BE+FE] **[DEFAULT R17]**
The system tells users about events that affect them through in-app notifications and transactional email, and the local environment captures email so every flow can be tested without an external mail service.
Source: PRD addition supporting USR-004, USR-005, AGN-008, AGN-009 and AGN-019 **[DEFAULT R17]**.
**Consequences (testable):**
- Transactional email is sent for email verification, password reset, email-change verification and deactivation decisions (FR-4, FR-5, FR-6, FR-8); in the local environment emails are captured by a development mail tool (for example a console or file mail backend or a local mail catcher, not production infrastructure) and are not delivered externally. Notification events arrive with the FR that produces them.
- An in-app notification list shows: Listing restricted, removed or restored (FR-63); suspension or reactivation (FR-59); Verification outcome (FR-62); Report outcome for the reporter (FR-65); support request closed (FR-71). A Deactivated account receives its approval by email, since it can no longer sign in.
- Notifications can be marked read and never contain another account's private data. New Messages appear through the unread indicator (FR-50), not as notifications.
- Email alerts for new Messages to offline recipients are not provided `[NON-GOAL: message alerts]`. `[NOTE FOR PM: offline recipients learn of new Messages only when they next open EstateAI; add email alerts if supervisors expect the proposal's lead-retention benefit.]`

**Feature-specific NFRs:** Passwords are never stored or logged in plain text. Sessions are protected against cross-site request forgery.

---

### 4.2 Listing Creation & Management
**Description:** One canonical Listing serves manual and AI-assisted creation. A Draft may be incomplete; completeness is enforced at Publication. Publication is immediate after automatic validation and Publication quota check, with no Administrator pre-approval. Realizes UJ-3, UJ-5.

#### FR-11: Create a Draft with purpose, Category and Subtype [P2] [BE+FE]
An Individual or Agency account can create a Draft, choosing sale or rent, a Category (House, Apartment, Plot, Commercial) and a supported Subtype.
Source: USR-026–028, SYS-006, SYS-007, SYS-008, AGN-023, AGN-024, AGN-032.
**Consequences (testable):**
- Every Listing belongs to exactly one owning account; Agency-created Listings belong to the Agency.
- Subtype must belong to the chosen Category; Plot File is distinct from a possession-ready plot.
- Manual and AI-assisted creation operate on the same Draft.

#### FR-12: Category-appropriate Listing details [P2] [BE+FE]
The creator can enter title, description, city and locality, area with unit, Asking price and the Category-specific attributes, and the form shows only fields relevant to the selected Category and Subtype.
Source: USR-029, USR-030, USR-032–034, SYS-009, AGN-026.
**Consequences (testable):**
- **Required for Publication (all Listings):** purpose, Category, Subtype, title, Asking price with price basis (rent also requires a rental period), city, locality, area with unit and at least one image.
- **Also required:** bedrooms and bathrooms for House and Apartment.
- Recommended and optional fields never block Publication; unknown values are never saved as zero or false.
- Supported area units include square feet, square yards, marla, kanal and acre; marla needs a conversion basis (225, 250 or 272.25 square feet) to be normalised. If it is unknown, Publication is not blocked, but the Listing is excluded from normalised-area range filtering and its AI Price Estimate is unavailable.
- Plot and Commercial Listings require only the common required fields plus their Subtype; their recommended and optional attributes are listed in addendum §8.
- The full attribute catalogue (addendum §8) stays in scope. Required, recommended and common optional fields arrive with this FR; installment, legal-document, video and floor-plan fields are **deferred to registry-driven extensions in P8 at the latest [DEFAULT R3]**; those extensions are built and tested like any other field (validation, form and, where searchable, search).

#### FR-13: Public location and Private address [P2] [BE+FE]
The creator can set city and locality from the supported reference list and may add an exact Private address.
Source: USR-031, SYS-011, SYS-012, AGN-044.
**Consequences (testable):**
- City and locality appear in Listing details for everyone, Visitors included `[amends SYS-011; see §10]`.
- The Private address and the account's phone number never appear in any public response; the owner may see the Private address, and an Administrator only with a recorded reason (FR-9).
- City and locality can only be chosen from the reference data, initially Karachi and its localities `[OQ-2]`.
- Society, phase, sector and block are part of the Public location (they support searches such as "DHA Phase 5"); street, house number and exact coordinates are private `[ASSUMPTION]`.

#### FR-14: Amenities and Seller-declared nearby facilities [P2] [BE+FE]
The creator can select Amenities and Seller-declared nearby facilities (mosque, park, gym, school, hospital).
Source: USR-035.
**Consequences (testable):**
- Each selection is stored as structured data usable by search and the AI Listing Assistant; unanswered is not treated as absent, and "none" can be explicitly recorded `[DEFAULT R4]`.
- Nearby facilities are labelled "seller-declared" wherever displayed; no distance, proximity claim or map verification is shown or computed.
- The AI Listing Assistant may fill these from the Seller's words (FR-26).

#### FR-15: Listing images [P2] [BE+FE]
The creator can upload multiple images, choose a cover image and order them.
Source: USR-036, USR-037, AGN-027, AGN-028, SYS-010.
**Consequences (testable):**
- A Draft may have no images; Publication is blocked without at least one valid stored image.
- Accepted files `[ASSUMPTION: JPEG, PNG, WebP; up to 5 MB each; up to 10 per Listing]`; invalid files are rejected with a clear message.
- Each Listing has at most one cover image; image metadata (including location) is stripped before storage, for Listing images, profile pictures and logos `[DEFAULT R7]`.
- Deleting the cover image promotes the next image in order; Publication is blocked if no image remains.
- Uploaded images are re-encoded and limited in pixel dimensions `[ASSUMPTION: 8000 x 8000]` and in storage per account `[ASSUMPTION: 200 MB per Individual account, 1 GB per Agency account]`, and are never served as executable content.

#### FR-16: Draft management and preview [P2] [BE+FE]
The creator can save an incomplete Draft, reopen and edit it, preview it as it will appear publicly and delete an unpublished Draft.
Source: USR-038–040, USR-085, AGN-029, AGN-030, AGN-041.
**Consequences (testable):**
- Saving never requires complete data.
- The preview shows the public view, including, once the valuation feature exists (P4), how the AI Price Estimate (if available) will appear.
- Deleting a Draft removes its data, images, Assistant session history and Draft estimate snapshots immediately (stored files within 24 hours); a published Listing cannot be deleted this way.
- A Draft with no edits for 180 days is treated as abandoned: the owner is notified and it is deleted 30 days later unless opened `[ASSUMPTION]`.

#### FR-17: Validation and immediate Publication [P2] [BE+FE]
The owner can publish a Draft; the system validates all mandatory information and the Publication quota and, if both pass, publishes immediately.
Source: USR-041–043, SYS-013, AGN-031.
**Consequences (testable):**
- Failure returns field-level errors naming each missing or invalid item; the Listing stays a Draft.
- No Administrator approval is required before a Listing is public.
- A suspended account cannot publish (FR-59); an Email-unverified account cannot either **[DEFAULT R9]** (FR-4).
- A failed or unavailable AI Price Estimate never blocks Publication (FR-43).
- A failed Publication consumes no quota.
- The Publication path accepts and checks per-field origin and confirmation data from the start, so AI-assisted Listings (FR-30) use the same path.

#### FR-18: Publication quota [P2] [BE+FE]
The system enforces a monthly Publication quota per account type, seeded at **2 for Individual accounts and 10 for Agency accounts**, and shows owners their allowance and remaining Publications.
Source: USR-082–084, SYS-014–017, ADM-044, AGN-079, AGN-040.
**Consequences (testable):**
- A third Publication by an Individual account (eleventh by an Agency account) in the same month is refused with a message stating the limit and the reset date.
- Two simultaneous Publication requests with one slot left result in exactly one success.
- A repeated or retried request does not consume a second slot and returns the original result.
- **[B24]** Correction window: within 24 hours of a counted Publication, the owner can correct the locality and area of the Published listing once, without archiving and without consuming a slot; Category, Subtype, purpose and city cannot be corrected this way. The correction is recorded as a revision.
- Society, phase, sector, block and the marla conversion basis are not identity fields. Republication after an Administrator returns a Listing to archived follows the same rules as any republication.
- **[B17]** Free same-month Republication applies only if the Listing's identity fields are unchanged since the Publication that was counted; if any changed while archived, the next Publication consumes a slot.
- **[DEFAULT R8]** The month is the calendar month in Karachi time; an initial Publication counts once; Republication in a different month counts; Republication within the same month for the same Listing does not consume another slot but is still recorded; quota changes apply only to later attempts; a Listing restored after moderation consumes nothing; editing a Published listing consumes nothing.

#### FR-19: Listing lifecycle and dashboard [P2] [BE+FE]
An owner can see all their Listings by Listing status, edit them, archive them and mark them sold or rented.
Source: USR-075–080, AGN-033, AGN-035–038.
**Consequences (testable):**
- Allowed transitions and who may perform them are defined in FR-75; "archive" is the only owner withdraw action.
- Owners can update any non-identity field of a Published listing (for example description, price, bedrooms, Amenities, nearby facilities, images); edits are validated by the rules of FR-17 and an edit that would make the Listing invalid is refused. Identity fields cannot be edited while published, except the one-time correction of FR-18 (FR-75).
- An owner's Draft and Conversations are not accessible to other accounts, and only the owner can edit its Published listing.

#### FR-20: Republication [P2] [BE+FE]
An owner can republish an eligible archived Listing, subject to revalidation and Publication quota.
Source: USR-081, AGN-039.
**Consequences (testable):**
- A sold or rented Listing must first be returned to archived.
- Republication is refused while the Listing's Visibility status is restricted (FR-75).
- Revalidation applies the same rules as FR-17; quota rules follow FR-18.

#### FR-21: Ownership and authorisation [P1] [BE]
The system prevents an account from viewing or changing Listings, Drafts, Assistant sessions, Conversations, AI Price Estimates, Reports, support requests, Verification requests and evidence, favourites or uploaded media that it does not own, participate in or is not authorised to see.
Source: USR-086, SYS-031, SYS-035, AGN-043.
**Consequences (testable):**
- Authorisation is enforced by the backend on every request; unauthorised attempts return not-found for Listings, Drafts, Assistant sessions and Conversations and access-denied for admin capabilities, and are covered by automated tests per endpoint.
- Public discovery returns only Published listings that are not restricted and whose owner is eligible (SYS-034 / FR-33).
- Media files of non-public Listings, Drafts, Verification evidence and profile data are not retrievable by non-owner, non-Administrator accounts or Visitors; images of Published listings are public.
- The authorisation test matrix grows with each phase that adds resources (P1 accounts, P2 Listings and media, P4 estimates, P5 Assistant sessions, P6 Conversations, P7 Reports, Verification and support).
- Any resource the caller is not entitled to see returns not-found (including Reports, support requests, Verification requests, notifications, estimates, media URLs and non-visible Agency profiles); endpoints that require sign-in return an unauthenticated response to Visitors.

#### FR-22: Edit conflict protection [P2] [BE]
When a manual edit and an AI update (or two sessions) change the same Draft or Listing, the system never silently overwrites newer accepted data.
Source: SYS-033; Domain §2.6–2.7 (optimistic concurrency).
**Consequences (testable):**
- A stale update is rejected with a conflict response that shows the current data; the user reloads and re-applies. The same rule applies to edits of a Published listing and to simultaneous sessions.
- Accepted changes record their origin (manual or AI) and whether the user has confirmed each AI-supplied value (used by FR-30).
- Drafts and Assistant sessions record the Field registry version they were created under; after a registry change an in-flight Draft is revalidated on its next edit, Published listings are unchanged and republication revalidates.

#### FR-23: Contact details kept out of Listing text [P2] [BE] **[DEFAULT R10]**
The system prevents seller and agency phone numbers from appearing on Listings.
Source: SYS-023, USR-096.
**Consequences (testable):**
- **[DEFAULT R10]** Phone-number patterns (including digits with separators, "+92" forms and spelled-out digits) and exact-address patterns in any public free text (Listing title and description, Agency name and description) are rejected with an explanation `[ASSUMPTION: pattern-based, best-effort; extends R10 to address patterns and Agency profile text]`; the check applies to AI-generated text too and uses the shared pattern specification of FR-32.
- Chat content is not scanned (users may share contact details voluntarily).

#### FR-75: Listing visibility and owner actions [P2] [BE+FE]
Who can see a Listing, and what its owner and an Administrator can do, are fully determined by the Listing status, the Visibility status and the owner's Account state. **[B18]**
Source: SYS-026, SYS-027, SYS-034, SYS-035, USR-076; Domain §2.5 (lifecycle and moderation visibility).
**Consequences (testable):**
- **Public visibility rule:** a Listing is publicly visible (in discovery, by direct link and for its media) if and only if its Listing status is published, its Visibility status is not restricted and its owner is Active (FR-74). Every other combination returns not-found to Visitors and other accounts.
- **Owner capabilities on its own Published listings by account state:** Active, all owner actions in the table; Suspended, view and archive only (no edits, no republication, no new Publication) and the Listings are hidden; Deactivated, none (the Listings are archived by the system, FR-8).
- A Suspended or Deactivated Agency's public profile and verified badge are not shown (FR-41); its Listings follow the rule above.
- Administrator actions in the table are implemented with FR-63 and FR-65 (P7); P2 delivers the owner side and the visibility rule.

| Listing status / Visibility status | In public discovery | Detail page by direct link (Visitor, other account) | Owner can | Administrator can |
|---|---|---|---|---|
| draft | No | Not-found | Edit, preview, publish, delete | View only with a recorded reason (FR-9) |
| published, not restricted | Yes (owner Active) | Visible (owner Active) | Edit permitted fields, archive, mark sold or rented | Restrict, remove |
| published, restricted | No | Not-found | View with reason, edit permitted fields, archive, mark sold or rented; cannot republish while restricted | Lift restriction, remove |
| archived | No | Not-found | Edit, republish (FR-20) | Restrict, view |
| sold, rented | No | Not-found | Return to archived | View |
| removed | No | Not-found | View with reason; no edits | Restore to published, move to archived |

- **Not-found** is the single response for every non-visible Listing, so the existence of a non-visible Listing is never revealed to unauthorised callers.
- Transitions: draft→published; published→archived, sold or rented (owner); published→removed (Administrator); archived→published (owner, FR-20); sold or rented→archived (owner, or the system when the owner is deactivated); removed→published (Administrator restore, no quota); removed→archived (Administrator, or the system when the owner is deactivated, FR-8). Administrators cannot remove a Draft (they suspend the account instead).
- *Under review* is a derived indicator that holds while at least one Report on the Listing is in reviewing (FR-65); it does not change visibility or owner rights, and resolving or dismissing a Report never changes the restricted flag.
- *Restricted* is set only by an Administrator and persists across status changes, including restoration of a removed Listing; **lifting a restriction** (visibility) and **restoring a removed Listing** (status) are distinct actions.
- Identity fields cannot be edited while a Listing is published, except the one-time correction of FR-18 **[B24]**; otherwise changing them requires archiving first, and the next Publication consumes a slot (FR-18). **[B17]**
- Favourites and Conversations of a non-visible Listing are kept; the favourites list shows such Listings as "no longer available" without their details.
- **Precedence:** owner state is evaluated first (Deactivated, then Suspended), then Listing status, then the restricted flag. The whole matrix (Listing status x restricted flag x owner state x viewer: Visitor, other account, owner, Administrator) is verified by a table-driven test (SM-6).

---

### 4.3 AI Listing Assistant (RAG-powered)
**Description:** Instead of the form, a Seller or Agency can hold a conversation. The AI Listing Assistant extracts details, asks only what is missing, uses real-estate knowledge where helpful and drafts the description. The assistant only *proposes*; validated values update the Draft, and the account owner approves before Publication. Realizes UJ-3, UJ-5.

#### FR-24: Start and choose AI-assisted creation [P5] [BE+FE]
An account can choose AI-assisted creation instead of the manual form and start an Assistant session in ordinary English.
Source: USR-044–046, AGN-025, AGN-045, AGN-046.
**Consequences (testable):**
- Choosing AI creates or opens one Draft and an Assistant session linked to it (one active session per Draft at a time).
- The user can leave and return to the manual form on the same Draft at any time (FR-29).

#### FR-25: Extraction and follow-up questions [P5] [BE+FE]
The AI Listing Assistant identifies Listing attributes from the user's messages, populates the Draft with validated values and asks follow-up questions for missing information, required fields first.
Source: USR-047–049, USR-052, AGN-047, AGN-048.
**Consequences (testable):**
- Information already given in one message (for example area, bedrooms, price) is not asked again.
- It asks for missing required fields for the selected Category and then offers optional questions without forcing the user to answer them.
- Values that fail registry validation are not saved; the user is asked to clarify.

#### FR-26: Seller-declared nearby facilities in conversation [P5] [BE+FE]
The AI Listing Assistant extracts Seller-declared nearby facilities and Amenities from natural language and asks about them when relevant.
Source: Register B8; USR-047.
**Consequences (testable):**
- "A masjid and park nearby" results in those facilities recorded as seller-declared; the assistant does not invent distances.
- Mentions that match no supported facility type are flagged to the user and not silently stored `[ASSUMPTION: see OQ-1]`.

#### FR-27: Context retention and corrections [P5] [BE+FE]
The assistant preserves details already supplied and lets the user correct them.
Source: USR-051, USR-053–055, AGN-050–052.
**Consequences (testable):**
- A later correction ("actually five bedrooms") replaces the earlier value in the Draft and conversation summary.
- Contradictory or ambiguous information triggers a clarifying question rather than a guess.

#### FR-28: Grounded real-estate knowledge (RAG) [P5] [BE]
The assistant draws on a curated real-estate knowledge base (Pakistani housing types, common Amenities, property measures and terminology such as marla, possession and balloting) to guide its questions and explanations when helpful.
Source: USR-050, AGN-049; Proposal §5.1.
**Consequences (testable):**
- Retrieved knowledge is never treated as proof of facts about the user's own property and never overrides validation.
- If retrieval is unavailable, the assistant still completes a Listing.
- Knowledge comes from curated, versioned sources (FR-78); each retrieved passage carries its source and version, and retrieved text is treated as untrusted data that cannot change instructions, validation or access rules.
- The assistant never claims legal verification (of approvals, titles or possession) on the strength of retrieved knowledge.
- On the 40 labelled domain questions (built with FR-78, finalised in FR-79), the correct passage is among the top 3 retrieved for at least 32 of them (80%) `[ASSUMPTION]` (SM-11).

#### FR-29: Description generation, review, switching and resume [P5] [BE+FE]
The assistant generates a professional description from confirmed details; the user can review and manually edit anything, switch between AI and manual on the same Draft, and save and resume an unfinished session.
Source: USR-056–061, AGN-053–056.
**Consequences (testable):**
- The description uses only confirmed details; it contains no invented facilities, amenities or claims. It is 2 to 4 short paragraphs of plain text (no links or markup) and is subject to FR-23.
- Manual edits survive a later AI turn unless the user changes them.
- A resumed session restores conversation history and Draft state.
- A repeated Assistant turn (same client turn identifier) is processed once.

#### FR-30: Integrity and explicit approval [P5] [BE+FE]
AI-extracted information passes the same validation as manually entered information, the assistant never invents missing facts, and an AI-assisted Listing requires explicit user approval before Publication.
Source: USR-062–065, AGN-057, AGN-058, SYS-020–022.
**Consequences (testable):**
- No AI path can publish a Listing; only the user's explicit confirmation action can.
- Required values and the sensitive values (Category, Subtype, purpose, Asking price, locality, area) are shown for confirmation before Publication and carry a confirmed flag (FR-22).
- An incomplete or invalid AI-generated Listing cannot be published, and an AI-supplied sensitive value that is not confirmed blocks Publication.

#### FR-31: Graceful degradation [P5] [BE+FE]
If the AI provider or knowledge retrieval is unavailable, slow or rate-limited, the user is told and can continue manually without losing the Draft.
Source: Domain §2.6; Register §4.
**Consequences (testable):**
- With AI disabled, the manual flow (FR-11 to FR-17) works fully (verified by fault-injection test).
- An AI call that exceeds its timeout (12 seconds for search, 20 seconds for an Assistant turn `[ASSUMPTION]`) is treated as unavailable. The user sees a clear message, and the Draft is identical to its state before the failed turn.

#### FR-32: AI usage controls and data protection [P5] [BE]
The system limits AI use per account and per visitor and does not send private data to the AI provider.
Source: Register §4; Domain §2.6.
**Consequences (testable):**
- Free-tier limits are not assumed unlimited: when limits are reached, users receive a clear message and fall back to conventional features.
- **[B20]** Phone number, email address and Private address are form-only: the AI Listing Assistant never asks for them, and if a user types one into a message it tells the user to use the form.
- **[B20]** Every user text sent to an AI provider (assistant messages, search queries, transcripts and any text sent for embeddings or description generation) first passes a redaction step that removes phone numbers, email addresses and exact-address patterns `[ASSUMPTION: pattern-based, best-effort for free-text addresses]`; Assistant session history stores the redacted text.
- **[B20]** Acceptance test: with canary phone, email and address values that conform to the shared patterns (the same specification as FR-23) in account data and in user messages, none appears in any recorded provider request; the patterns are also tested against a false-positive set so prices, areas and counts are preserved.
- Voice audio is sent to the speech-to-text provider with no account data attached. Audio is outside redaction and is covered by the disclosure; speech recognition runs through this server-side path, and browser-native recognition, which would bypass the disclosure, is not used `[ASSUMPTION]`.
- Input length is capped (assistant message 2,000 characters, search query 500 characters `[ASSUMPTION]`); AI-generated text is rendered as plain text, and Listing text is never given to a model as instructions.
- The provider and model identifier of each AI call are recorded in the AI request log (no content), supporting FR-68.
- The UI discloses that AI features use an external service before first AI use (text or voice) by any account or Visitor `[ASSUMPTION: short notice]`.

#### FR-78: Real-estate knowledge base [P5] [BE]
The project provides the curated real-estate knowledge base that FR-28 depends on.
Source: Proposal §5.1; Domain §2.6.
**Consequences (testable):**
- Every passage is authored or explicitly reviewed (no unreviewed scraped text) and records title, version and provenance.
- Coverage includes Pakistani housing types, common Amenities, measures (marla, kanal, square yard), possession, balloting, registry and inteqal terminology, and guidance on describing a property.
- Size is at least 100 passages `[ASSUMPTION]`, and the index can be rebuilt reproducibly from the sources.


---

### 4.4 Search & Discovery
**Description:** Anyone can browse and search Published listings without signing in, using conventional filters, natural-language text or voice. Natural language is interpreted into structured filters, shown to the user and executed safely against validated data. Realizes UJ-1, UJ-2.

#### FR-33: Public browsing [P3] [BE+FE]
Visitors and signed-in accounts can browse Published listings and view full Listing details without signing in.
Source: USR-011, USR-019, SYS-005, SYS-034, AGN-059, AGN-063.
**Consequences (testable):**
- Draft, archived, sold, rented, removed, restricted and ineligible-owner Listings never appear in public discovery, and their detail pages return not-found to Visitors and non-owner accounts (FR-75, FR-74).
- Listing details show images, description, attributes, Amenities, Seller-declared nearby facilities and Public location but not Private address or phone.

#### FR-34: Conventional filters and sorting [P3] [BE+FE]
Users can filter by city, locality, Category, Subtype, listing purpose, price range, area range, bedrooms, bathrooms, parking, Amenities and Seller-declared nearby facilities (combinable, driven by the Field registry) and sort by price and listing date; results are paginated.
Source: USR-012–014, USR-018, AGN-060.
**Consequences (testable):**
- Filters combine (logical AND) and results are deterministic and paginated.
- Filtering works with AI features disabled, and every criterion natural-language search can apply (FR-35) can also be set with conventional filters, so the AI-off fallback loses no filtering capability.

#### FR-35: Natural-language text search [P5] [BE+FE]
Users can search with free text and receive matching Published listings.
Source: USR-015, USR-017, AGN-061.
**Consequences (testable):**
- Supported criteria include Category, purpose, locality, price bounds, area, bedrooms, bathrooms, parking, Amenities and Seller-declared nearby facilities.
- "At least 4 bedrooms" means 4 or more; "exactly 4" means 4; "under 2 crore" is an upper price bound.
- The interpretation never produces executable database text from the model; only validated supported filters run.
- Results are ordered by the number of stated preferences met, then newest first; the user can instead sort by price or listing date (FR-34).
- A Listing without a marla conversion basis is excluded when the query depends on normalised area (FR-12).

#### FR-36: Interpretation transparency and unknown data [P5] [BE+FE]
The system shows the Search interpretation, lets the user edit it, distinguishes required criteria from preferences and handles missing data explicitly.
Source: Domain §2.6 (search rules).
**Consequences (testable):**
- Unsupported criteria are named in the response, and the rest of the query still runs.
- A Listing that never stated parking is **not** treated as having none: for a required criterion it is excluded; for a preference it may appear ranked lower.
- Nearby-facility matches are labelled seller-declared.
- A criterion is **required** when it is a price bound, Category, purpose or locality, a bedroom or bathroom count stated with "at least", "exactly" or "under", or anything the user marks with "must" or "need"; all other criteria are preferences `[ASSUMPTION]`.

#### FR-37: Voice search [P5] [BE+FE]
Users can speak a query; it is transcribed to text, shown for correction and then handled as in FR-35.
Source: USR-016, AGN-062; Proposal §5.3.
**Consequences (testable):**
- The transcript is visible and editable before search.
- English is supported; Urdu or Roman Urdu are best-effort and their measured quality is reported rather than promised; English word error rate is reported, not gated.
- Audio is not stored by EstateAI after transcription `[ASSUMPTION]`; it is sent to the speech-to-text provider under the FR-32 rules.
- Failure to access the microphone or to transcribe falls back to text search.

#### FR-38: Search availability and abuse control [P5] [BE+FE]
Natural-language and voice search remain usable when AI capacity is limited.
Source: Register §4.
**Consequences (testable):**
- Per-client throttling (limits in NFR-10) and a global budget guard that reserves capacity for signed-in Listing creation prevent a single client from exhausting AI capacity; Visitors are identified by network address and a session cookie `[ASSUMPTION]`.
- On throttle, timeout or provider failure, the user is offered conventional filters with their text retained.

#### FR-39: Listing detail with provenance [P3] [BE+FE]
Listing details distinguish Individual-owned from Agency-owned Listings, show Agency name and branding and, where granted, Verification status.
Source: USR-019–021, AGN-042, AGN-063.
**Consequences (testable):**
- Result cards in search and browsing also show whether a Listing is owned by an Individual or an Agency (with its name).
- An Agency Listing shows the Agency name; verified Agencies show a verified indicator; unverified Agencies show none.
- Seller-declared and unverified legal or approval statements are labelled "seller-declared" and never carry an EstateAI badge.

#### FR-40: Favourites [P3] [BE+FE]
An Individual or Agency account can save Published listings to a favourites list, view it and remove items.
Source: USR-024, USR-025.
**Consequences (testable):**
- Visitors cannot keep favourites (prompted to register).
- Only eligible Published listings can be newly saved; removing a favourite does not affect the Listing.
- Listings that become non-visible stay in the list as "no longer available" without details (FR-75). Administrators have no favourites.

#### FR-41: Agency public profile and Agency Listings [P3] [BE+FE]
Users can view an Agency's public profile and browse its Published listings.
Source: USR-099, USR-100, AGN-063.
**Consequences (testable):**
- The profile shows name, logo, description, Verification status and its Published listings only.
- Business phone and private fields never appear.
- A Suspended or Deactivated Agency's profile and verified badge are not shown (FR-75).

---

### 4.5 Property Valuation (AI Price Estimate and 5-Year Price Forecast)
**Description:** The AI Price Estimate gives Sellers a baseline during Listing creation and shows buyers the Estimated Property Value next to the Asking price, with a 5-Year Price Forecast. The model is trained on synthetic data; the interface stays clean and consistent with a modern marketplace, giving a concise, unobtrusive explanation of limitations, while data, methodology and limitations are documented in the technical documentation. The Asking price is never overwritten. Realizes UJ-1, UJ-3.

#### FR-42: Request an AI Price Estimate [P4] [BE]
An authenticated account can request an AI Price Estimate for property attributes it enters or for its own Draft. For a Published listing, users see the stored estimate (FR-44).
Source: USR-066, USR-067, AGN-064.
**Consequences (testable):**
- The estimate uses validated Listing attributes only; Private address, phone and the Asking price are not model inputs.
- Each estimate records the model version and inputs used so it can be reproduced.
- A response is returned within the budget in §8 NFR-1.
- **[B22]** Requests for a Draft or entered attributes are throttled per account `[ASSUMPTION: 20 per hour]`; Visitors cannot trigger computation.
- **[B22]** On Publication the system computes and stores the Published listing's estimate from the Published listing's model-input snapshot; it never reuses an estimate requested earlier for a Draft.

#### FR-43: Baseline during Listing creation [P4] [BE+FE]
During creation, the creator can view the AI Price Estimate as a recommended baseline price without it being required.
Source: USR-068, USR-074, AGN-067, SYS-018.
**Consequences (testable):**
- A valid Draft can be published whether or not an estimate is available or requested.
- The Asking price field is never changed by the estimate.
- The baseline is requested by an explicit action, not on every field edit, so it stays within the estimate throttle of FR-42.

#### FR-44: Compare Asking price with the Estimated Property Value [P4] [BE+FE]
Users can see the Estimated Property Value next to the Asking price and whether the Asking price is above or below it.
Source: USR-022, USR-069, USR-070, USR-073, AGN-065, SYS-019.
**Consequences (testable):**
- The two figures are visibly distinct and labelled "Asking price" and "Estimated Property Value"; no repeated banner accompanies them.
- The above/below indicator appears only when an estimate is available.
- The comparison uses the total price (a per-unit price basis is converted using the Listing's area); the indicator shows "about equal" within ±5% `[ASSUMPTION]` and above or below, with the percentage difference, otherwise.
- **[B22]** The displayed estimate is the stored result bound to the model-input snapshot of the Published listing; Visitors and other accounts read it without triggering computation.
- **[B22]** When an edit changes a model input of a Published listing (for example Amenities), the estimate is recomputed; until then, or if recomputation fails, it is shown as unavailable rather than stale.
- If computation fails at Publication or recomputation fails, the owner can request recomputation. Stored estimates keep their model version; a new model version applies to estimates computed after it is deployed, and the owner can request recomputation.

#### FR-45: 5-Year Price Forecast [P4] [BE+FE]
Users can view a 5-Year Price Forecast for a supported Listing where available.
Source: USR-023, USR-071, AGN-066; Proposal §5.2.
**Consequences (testable):**
- The forecast is never presented as a guaranteed return or validated investment prediction; its synthetic assumptions are documented in the technical documentation.
- It is presented with a low, base and high scenario range **[DEFAULT R13]**.

#### FR-46: Unavailable and uncertain results [P4] [BE+FE]
When data is insufficient or the Listing is not supported, the system says so instead of showing a number.
Source: USR-072.
**Consequences (testable):**
- Unsupported Category, purpose or locality yields a clear "estimate unavailable" state, never a guessed value.
- **Supported** means: sale purpose; a (Category, Subtype) pair of (House, House), (Apartment, Apartment) or (Plot, Residential Plot); a Karachi locality covered by the training data; and a known marla basis where the area unit is marla. Commercial Plot, Plot File, Shop and Office are unavailable until the model covers them (FR-77) `[ASSUMPTION]`.
- **[DEFAULT R13]** v1 estimates apply to sale Listings; rent Listings show "unavailable".

#### FR-47: Concise limitation note and documented methodology [P4] [FE]
Wherever an Estimated Property Value or 5-Year Price Forecast appears, the product gives one concise, unobtrusive explanation of its limitations, and the methodology is documented.
Source: Register B13.
**Consequences (testable):**
- The note is an inline info affordance (for example an icon with a short popover), not a banner; it states that the figures are AI-generated approximations from Listing attributes, not verified market valuations or guaranteed returns.
- The words "demo" and "demonstration" do not appear in the estimate or forecast presentation, and no persistent or repeated disclaimer banner is shown.
- The popover links to a short "About AI Price Estimates" page, a required deliverable delivered with this FR (FR-80 holds the full documentation), that states the figures come from a model trained on synthetic Karachi data, what the estimate uses and its limitations. The one-line popover omits the synthetic-data statement by design (register B13: no repeated labels). `[NOTE FOR PM: deliberate trade-off between a clean interface and full disclosure; revisit if supervisors expect the statement in the popover.]`
- The documentation states that estimates approximate asking prices, not transaction prices (Domain §2.6).
- Synthetic data, generator assumptions, model limitations, forecast scenario assumptions and evaluation methodology are documented in the technical documentation, which also states the covered area (Karachi) and the model version.

#### FR-77: Synthetic dataset and valuation model [P4] [BE]
The project provides the synthetic Karachi dataset, its generator and the trained, versioned valuation model that FR-42 to FR-47 depend on.
Source: Register B13, A8; Proposal §5.2, §6.
**Consequences (testable):**
- The generator is reproducible (fixed seed) and its assumptions (per-locality base rates, multipliers, noise) are documented.
- The model excludes Private address, phone and Asking price from its inputs, and is registered with a model version and a feature-schema version.
- The Categories and Karachi localities the model covers are stated in the technical documentation (FR-80), and the forecast scenario parameters are documented and reproducible.
- Evaluation follows SM-4.


---

### 4.6 Real-Time Messaging
**Description:** Buyers contact Listing owners inside EstateAI. Conversations are private, tied to one Listing, delivered in real time and persisted. Phone numbers are never needed. Realizes UJ-4.

#### FR-48: Start an Inquiry [P6] [BE+FE]
An Individual or Agency account can start a Conversation with the owner of a Published listing.
Source: USR-087, USR-091, USR-095, AGN-068, AGN-069, AGN-072.
**Consequences (testable):**
- Each Conversation names the Listing it concerns.
- An account cannot start a Conversation about its own Listing.
- There is at most one Conversation per Listing and initiating account; starting again opens the existing one.
- Agency accounts may both receive and start Conversations.
- A Conversation accepts new Messages only while its Listing is a Published listing and neither participant is Deactivated; sending by a Suspended participant is refused (FR-74). Otherwise it is read-only for both participants, and starting again on a non-visible Listing opens the existing thread read-only. The Conversation header shows the Listing title only while the Listing is publicly visible; otherwise it shows "Listing no longer available" (or "Sold" / "Rented"). If the Listing becomes visible again, for example when a restriction is lifted, the thread accepts Messages again **[B25]**.
- A Message is limited to 2,000 characters `[ASSUMPTION]`; blocking or muting users is out of scope `[NON-GOAL]`.

#### FR-49: Real-time text messages [P6] [BE+FE]
Participants can send text Messages and receive them in real time.
Source: USR-088, USR-089, AGN-070.
**Consequences (testable):**
- A recipient who is online sees a new Message without refreshing, within the budget in §8 NFR-1.
- Empty Messages are refused; retrying a send never creates duplicates.
- A Message is reported delivered only after it has been saved.

#### FR-50: Inbox, history and unread [P6] [BE+FE]
Participants can view all their Conversations, full history and which Conversations have unread Messages.
Source: USR-090, USR-092, USR-093, AGN-071, AGN-073, AGN-074.
**Consequences (testable):**
- Unread state clears when the Conversation is opened.
- On reconnect, missed Messages appear without loss or duplication.
- An Agency's Conversations are listed in its inbox, which the Agency dashboard (FR-55) links to.

#### FR-51: Conversation privacy [P6] [BE]
Only the two participants can read or send Messages in a Conversation.
Source: USR-094, AGN-075, SYS-025, SYS-035.
**Consequences (testable):**
- A third account, a Visitor and a non-participating Administrator cannot read Conversation content through any API route, WebSocket or admin view other than a reasoned Report read (FR-54).
- Automated tests cover participant, counterpart and intruder over both the request and real-time paths.

#### FR-52: Contact privacy and internal-only communication [P6] [BE]
Seller and Agency phone numbers are never exposed in Listings or Conversations by the system, and buyer–seller communication happens through EstateAI.
Source: USR-096, SYS-023, SYS-024.
**Consequences (testable):**
- No Listing response, Agency profile, Conversation payload or other-account-facing response includes any account's stored phone number; phone numbers a user types into a Message are that user's own content and are not scanned.
- Automated scan of public responses finds zero phone numbers or Private addresses (SM-6).

#### FR-53: Conversation continuity and restrictions [P6] [BE]
Conversations are kept when a Listing is archived, sold or rented, and suspended accounts cannot send Messages.
Source: Domain §2.2 (Communication); SYS-030.
**Consequences (testable):**
- History remains readable by participants after the Listing's status changes.
- A suspended account's send attempt is refused with a clear reason; it can still read its Conversations (FR-74).
- A counterpart cannot send Messages into a Conversation whose other participant is Deactivated; the sender is told the user is no longer available.

#### FR-54: Report a Conversation or Message [P7] [BE+FE] **[DEFAULT R10]**
A participant can report abusive or inappropriate interactions; a Conversation Report requires the reporter to select the Message(s) at issue.
Source: USR-097, AGN-076.
**Consequences (testable):**
- **[DEFAULT R10]** The Report goes to Administrators, who see Conversation content only through the Report (no routine access).
- **[B21]** They see only the reported Message(s) and up to 5 preceding Messages for context `[ASSUMPTION: 5 Messages]`, with a recorded reason (FR-9); each view is audited (FR-69).
- The reporter receives confirmation that the Report was recorded.

---

### 4.7 Agency Operations
**Description:** Agency accounts use the same Listing, search, valuation and messaging as individuals, under Agency ownership and quota, plus a dashboard and optional Verification. Realizes UJ-5.

#### FR-55: Agency dashboard [P7] [BE+FE]
The Agency account has a dashboard showing total Listings, Listings by Listing status, the Publication quota and remaining allowance, and Inquiries received (overall and per Listing), and can filter its Listings by status, Category, location and date.
Source: AGN-010, AGN-034, AGN-077–081.
**Consequences (testable):**
- Counts are consistent with the Agency's Listings and Conversations at the time shown.
- Filters combine; the dashboard is visible only to the Agency account.

#### FR-56: Listing performance statistics [P7] [BE+FE] **[DEFAULT R14]**
The Agency can see Listing performance statistics: a view count per Listing.
Source: AGN-082.
**Consequences (testable):**
- Each Published listing detail view increments a view count shown to its owner; repeated views by the same session within 30 minutes count once `[ASSUMPTION]`, and viewers are never identified or exposed.

#### FR-57: Agency Verification request [P7] [BE+FE]
An Agency can operate without Verification, may submit a Verification request with business information, see its status and outcome and resubmit after rejection.
Source: AGN-015–019, AGN-022, SYS-028.
**Consequences (testable):**
- Agencies without Verification can publish and message normally (subject to email verification, FR-74).
- At most one pending request per Agency; statuses are visible (unverified, pending, verified, rejected, revoked, withdrawn); an Agency can withdraw a pending request and submit a new one after rejection or revocation.
- Verification evidence is visible only to the submitting Agency and to Administrators with a recorded reason (FR-9), and is retained per §8.3.
- What business evidence is required is an open product question `[OQ-3]`.

#### FR-58: Verified status display [P7] [BE+FE]
Only an Administrator can grant or revoke Verification; verified status appears on the Agency's public profile and Listings.
Source: AGN-020, AGN-021, SYS-029.
**Consequences (testable):**
- No Agency-side action can set verified status.
- Revocation removes the indicator everywhere but leaves the Agency's Listings published.

---

### 4.8 Administration & Governance (custom admin area)
**Description:** Administrators use a dedicated, protected area of the web app. Every capability is enforced by the backend. Moderation is after-the-fact, not a pre-publication gate. Realizes UJ-6.

#### FR-59: Account suspension and restrictions [P2] [BE+FE]
An Administrator can suspend or reactivate Individual and Agency accounts and apply supported restrictions.
Source: ADM-010, ADM-012, ADM-023, SYS-030.
**Consequences (testable):**
- Supported restrictions in v1 are suspension only; finer restrictions (publish-only or message-only) are not provided `[ASSUMPTION]`.
- A suspended account is in the Suspended state (FR-74): it cannot publish, send Messages or edit Drafts; the reason is shown to the account at sign-in.
- Listings of a suspended owner are not publicly discoverable.
- Each action creates an Audit record (FR-69).

#### FR-60: Quota policy management [P2] [BE+FE]
An Administrator can view and change the monthly Publication quota separately for Individual and Agency accounts without code changes, and view usage by account.
Source: ADM-038–043, SYS-014.
**Consequences (testable):**
- Seed values are 2 and 10; a change affects only later Publication attempts.
- Every change is recorded in an Audit record with the actor and old and new values.

#### FR-61: Account directory and review [P7] [BE+FE]
An Administrator can view, search and filter Individual and Agency accounts, see status and profile details, review an account's Listings and review account-related Reports and deactivation or deletion requests.
Source: ADM-007–009, ADM-011, ADM-013–017, ADM-024.
**Consequences (testable):**
- "Account-related Reports" means Reports on that account's Listings, Conversations or Messages.
- **[B21]** Private fields (phone, Private address) are visible to an Administrator only after entering a reason, which is recorded (FR-9, FR-69); there is no bulk export of private data.

#### FR-62: Agency Verification review [P7] [BE+FE]
An Administrator can review pending requests and submitted information, approve, reject (with a recorded reason) or revoke Verification, and see decision history.
Source: ADM-018–022, ADM-025.
**Consequences (testable):**
- Rejection requires a reason shown to the Agency; every decision is recorded with the Administrator and time.

#### FR-63: Listing moderation [P7] [BE+FE]
An Administrator can view all non-Draft Listings (Drafts only with a recorded reason, FR-9), filter by owner, Category, Listing status and date, review information and images, remove violating Listings, temporarily restrict serious-risk Listings, restore Listings and record reasons.
Source: ADM-026–028, ADM-031, ADM-032, ADM-035, ADM-036, SYS-026, SYS-027.
**Consequences (testable):**
- A reported Listing normally remains visible until an Administrator acts.
- A restricted or removed Listing disappears from public discovery and its detail page (FR-75). An Administrator can lift a restriction (visibility) or restore a removed Listing (status); they are distinct actions and each records a reason.
- Every Moderation action stores the actor, reason, time and outcome.
- The Listing owner is told when a Listing is restricted or removed, with the reason.

#### FR-64: Report a Listing [P7] [BE+FE]
An Individual or Agency account can report a suspicious, misleading or inappropriate Listing, including reporting AI-generated content within it.
Source: USR-098.
**Consequences (testable):**
- The Report records a reason from a controlled list `[ASSUMPTION: misleading, duplicate, prohibited content, abusive, AI-generated content, other]` and an optional description.
- A reporter can report only Published listings they can see; an owner cannot report their own Listing; at most one open Report per reporter per Listing; the reporter's identity is not shown to the owner.
- A Report stores a snapshot of the reported content as evidence: for a Listing, its public-view content only (never the Private address or phone); for Messages, only the reported Message(s) plus up to 5 preceding ones, selected by the reporter for a Conversation Report.
- An Administrator viewing a snapshot that contains Message content must enter a reason, which is recorded (FR-9, FR-69); Message snapshots are anonymised when a participant account is deactivated (§8.3).
- Submitting a Report never removes the Listing automatically.

#### FR-65: Report review [P7] [BE+FE]
An Administrator can view Reports with their reason and evidence, mark them resolved or dismissed and see unresolved counts.
Source: ADM-029, ADM-030, ADM-033, ADM-034, ADM-056.
**Consequences (testable):**
- Report status moves pending→reviewing→resolved or dismissed, with notes and timestamps.
- While at least one Report on a Listing is in reviewing, the Listing shows as under review (a derived indicator); resolving or dismissing a Report never changes the Listing's restricted flag (FR-75).
- Administrators can filter Reports by reason, including AI-generated content (ADM-056), and view the content snapshot as evidence.
- For a Conversation or Message Report the outcomes are dismiss or resolve with notes, optionally followed by a suspension (FR-59); Administrators cannot edit or delete Messages.

#### FR-66: Suspicious and duplicate Listing flags [P7] [BE+FE] **[DEFAULT R14]**
Administrators can review Listings flagged as suspicious or duplicate.
Source: ADM-037.
**Consequences (testable):**
- Duplicate flagging uses simple documented rules (for example same owner, locality, area and near-identical price or title); a Listing is flagged suspicious when two or more open Reports from different reporters arrive within 7 days `[ASSUMPTION]`. Flags are advisory only.

#### FR-67: Reference data management [P7] [BE+FE] **[DEFAULT R5]**
An Administrator can manage the Amenity and Seller-declared nearby facility catalogue and curated city and locality data; the system prevents unsafe deletion of reference data in use.
Source: ADM-045–050.
**Consequences (testable):**
- Items in use are deactivated, not deleted.
- **[DEFAULT R5]** Categories, Subtypes, listing purposes and area units are fixed by the product and visible to Administrators but not editable (amends ADM-045, ADM-047 and ADM-048 to "view").
- Karachi and an initial locality list are available from the first run `[OQ-2]`.

#### FR-68: AI and ML oversight [P7] [BE+FE] **[DEFAULT R14]**
Administrators can see AI feature status, aggregated AI request failures, deployed valuation model version information and failed valuation requests without exposing private data, and can disable a malfunctioning AI feature.
Source: ADM-051–055.
**Consequences (testable):**
- Aggregates exclude conversation content and personal data.
- AI-related Reports are reviewed through FR-65 (reason filter), alongside AI feature status.
- Disabling a feature leaves manual and conventional alternatives working (FR-31).

#### FR-69: Audit trail [P7] [BE+FE]
Security-sensitive and policy actions are recorded and Administrators can review them.
Source: ADM-006, ADM-064, SYS-032.
**Consequences (testable):**
- Audit records are not editable or deletable through the product.
- **[B21]** Records cover: Administrator provisioning, sign-in, failed sign-in and password changes; suspensions; deactivation decisions; Verification decisions; Moderation actions and Report status changes; quota policy changes; reference-data changes; AI feature disabling; and every reasoned read of private data (phone, Private address, Draft, Verification evidence, Report-linked Messages).
- Audit capture is available from the phase that first needs it (P1/P2, for FR-9, FR-59 and FR-60); the review screen is delivered in P7.

#### FR-70: Administrator dashboard [P7] [BE+FE]
The dashboard shows total Individual accounts, total Agency accounts, total Published listings, Listings by Listing status, pending Verification requests, unresolved Reports and relevant platform activity.
Source: ADM-057–063.
**Consequences (testable):**
- Counts match the underlying data at load time.

#### FR-71: Support requests [P7] [BE+FE] **[DEFAULT R14]**
An Individual or Agency account can submit a platform support request, and Administrators can see and close it.
Source: USR-101.
**Consequences (testable):**
- The submitter sees the request recorded; Administrators can mark it closed.

---

### 4.9 Sample Data, Evaluation & Documentation
**Description:** The application must be fully usable and testable on a development machine with realistic sample data. Seed tooling is built incrementally from P2 and completed in P8. Realizes all UJs.

#### FR-72: Sample marketplace data [P8] [BE+FE]
The system can be loaded with a repeatable sample Karachi marketplace: Individual and Agency accounts, Listings across all four Categories with images, localities, Amenities, Seller-declared nearby facilities and valuation data for the Categories and localities the model supports (FR-77).
Source: Register §4, B5, B13.
**Consequences (testable):**
- Loading sample data does not consume any account's Publication quota and can be repeated safely.
- Sample records are identifiable internally so they can be removed or reseeded; no end-user banner labels them.
- The sample data supports search, valuation, messaging and moderation testing.
- Seed tooling is built incrementally from P2 (accounts and Listings), P4 (valuation data) and P5 (evaluation data) and completed here.

#### FR-73: Local operation [P8] [BE+FE]
The full product (frontend and backend) can be set up, run, tested and presented on a development machine without deployment.
Source: Register A6.
**Consequences (testable):**
- A fresh clone of both repositories can be brought to a working, seeded environment by following the documentation on a machine meeting the documented requirements `[ASSUMPTION: within one hour]`.

#### FR-79: Evaluation assets and harness [P8] [BE]
The project provides labelled evaluation sets and a repeatable harness for the success metrics.
Source: Proposal §7 (testing); Register B13.
**Consequences (testable):**
- Sets: at least 100 natural-language queries (at least 60 in English, plus Roman Urdu and Urdu, including negative cases), 30 scripted Assistant scenarios (including corrections, contradictions, missing data and prompt-injection attempts), exactly 40 domain questions for retrieval, and a held-out valuation split. Labels are reviewed by a second person where available `[ASSUMPTION]`.
- A performance fixture of about 10,000 Listings without images (NFR-1) is generated for load testing.
- The harness runs against a recorded provider stub for deterministic results and optionally against live providers, and writes a results report.
- Each set is built with the feature it tests (P4, P5) and finalised here.

#### FR-80: Technical documentation [P8] [BE+FE]
The project provides technical documentation covering architecture, the API contract, synthetic data, the model, evaluation and limitations.
Source: Register B13, A5.
**Consequences (testable):**
- Covers the synthetic data generator assumptions, model limitations, forecast scenario assumptions, evaluation methodology and results, the full documentation behind the "About AI Price Estimates" page delivered in FR-47, setup instructions (FR-73) and the privacy and AI-egress design (FR-32).
- Written incrementally with each phase and complete at P8.

---

## 5. Non-Goals (Explicit)
- **No payments, subscriptions, escrow or monetisation** (register B9). Quotas are a policy, not a paywall.
- **No claim of verified valuation or guaranteed returns.** The AI Price Estimate and 5-Year Price Forecast are academic-prototype outputs.
- **No geographic proximity features:** no distance calculation, geographic verification, map-based search, external Places data or PostGIS (register B8). Seller-declared nearby facilities only.
- **No Agency employee seats:** employees share one Agency account; no per-employee identity, roles or audit **[DEFAULT R15]**.
- **No non-English interface** (register B10); voice and text queries in Urdu/Roman Urdu are best-effort only.
- **No Administrator pre-approval of Listings;** moderation is after publication.
- **No third-party sign-in, SMS verification, in-chat file attachments, voice calls or push notifications** in v1.
- **No Administrator-owned Listings;** no admin registration.
- **No locally hosted LLMs;** AI uses hosted services (register A9).
- **Not a transaction platform:** EstateAI connects parties; deals and documents happen outside it.

## 6. Scope and Implementation Phases

### 6.1 In Scope
All of FR-1 to FR-80 are in scope for the complete application. EstateAI is a complete, polished academic prototype, not a staged MVP, and no requirement is cut or deferred for deadline reasons. `[NOTE FOR PM: if delivery risk appears, the items to build last (reordering within P7 and P8 only, never removal) are FR-10, FR-56, FR-66, FR-68, FR-70 and the participant-based evaluations SM-9 and SM-10.]`

### 6.2 Delivery approach
A sole developer builds the product with Claude Code, in small, testable, sequential tasks. Frontend and backend are separate repositories planned together; each phase covers both and ends in a tested, runnable increment.

### 6.3 Implementation phases **[DEFAULT R16]**
Phases follow architectural dependency. They sequence work for one developer and carry no scope decision.

| Phase | Name | FRs | Depends on | Exit check |
|---|---|---|---|---|
| P1 | Identity and platform foundation | FR-1 – FR-7, FR-9, FR-21, FR-74, FR-76, audit capture (part of FR-69) | none | All three Account types authenticate; authorisation framework and admin area access proven by tests |
| P2 | Listings and publication | FR-11 – FR-20, FR-22, FR-23, FR-75, FR-59, FR-60 | P1 | A Listing is created manually, validated, published within quota and managed through its lifecycle; suspension and quota policy enforced |
| P3 | Discovery | FR-33, FR-34, FR-39 – FR-41 | P2 | Public browsing, filters, favourites and Agency profile work on real data |
| P4 | Valuation | FR-42 – FR-47, FR-77 | P2 | AI Price Estimate and 5-Year Price Forecast appear in creation and detail views |
| P5 | AI capabilities | FR-24 – FR-32, FR-35 – FR-38, FR-78 | P2, P3, P4; hosted providers chosen after a short availability and structured-output trial | AI Listing Assistant, RAG, natural-language and voice search work and degrade gracefully |
| P6 | Messaging | FR-48 – FR-53 | P1, P2 | Private real-time Conversations pass the participant-access tests |
| P7 | Agency operations and governance | FR-8, FR-10, FR-54 – FR-58, FR-61 – FR-71 | P2, P3, P6 | Agency dashboard, Verification, moderation, Reports, admin dashboard, audit and AI/ML oversight complete |
| P8 | Integration, sample data and evaluation | FR-72, FR-73, FR-79, FR-80, deferred attribute extensions of FR-12 | P1 – P7 | UJ-1 to UJ-6 run end to end on a seeded environment; SM-1 to SM-11 measured |

After P2, P3, P4 and P6 are independent of each other; the order shown is a recommendation. Each FR is tagged [BE] (backend only), [FE] (frontend only) or [BE+FE] (both). Cross-phase dependencies: the verified indicator in FR-39 appears once FR-58 exists; FR-43 embeds the estimate into the creation form after P4; FR-54 depends on the Report capability of FR-64 and FR-65; natural-language search (FR-35) depends on the Field registry and Listing data from P2; FR-7 and FR-50 refer to the Agency dashboard (FR-55, P7) only as a later consumer; FR-16's estimate preview, FR-39's verified indicator and FR-72's seed tooling are completed in the later phase that owns them; FR-6 and FR-7 uploads apply the file rules of FR-15 and the contact check of FR-23 once P2 lands; FR-74's request capabilities and FR-76's events arrive with FR-8, FR-71 and the other FRs that produce them; the About-page content ships with FR-47 (P4) and FR-28's question set is built with FR-78 (P5).

---

## 7. Success Metrics
*Targets are `[ASSUMPTION]` starting thresholds reviewed after the first evaluation run (FR-79), not commitments from the proposal.*

**Primary**
- **SM-1: End-to-end journeys.** On a freshly seeded local environment, UJ-1 to UJ-6 each execute end to end with zero manual database or code interventions, in two consecutive runs, using the recorded provider stub for AI and speech services; a separate smoke check against live providers is reported but not gated. Validates the journeys; FRs the journeys do not exercise are verified by their own testable consequences.
- **SM-2: AI Listing Assistant accuracy.** On 30 scripted seller scenarios, at least 90% of required fields are extracted correctly, and 0 unconfirmed or invented values reach a Published listing; every claim in a generated description traces to a confirmed Listing field (per-claim checklist); correction and contradiction scenarios resolve to the user's latest confirmed value; the scenarios include prompt-injection attempts. Measured against live providers (reporting provider, model, date and repeated runs); the recorded stub is for regression only. Validates FR-25 – FR-30. `[ASSUMPTION]`
- **SM-3: Natural-language search quality.** On a labelled set of at least 100 queries (at least 60 in English), filter-extraction F1 ≥ 0.85 for English; Urdu/Roman Urdu results reported, no threshold. The share of model outputs rejected or repaired by validation is reported, and 100% of executed queries use only validated filters. F1 is micro-averaged over (filter, value) pairs across the query set. Measured against live providers (reporting provider, model, date and repeated runs). Validates FR-35 – FR-37. `[ASSUMPTION]`
- **SM-4: AI Price Estimate and forecast checks.** (a) Model: on a held-out synthetic set, MAPE ≤ 20% and better than a median-price-per-area baseline; this is a pipeline-correctness check and the technical documentation states that results reflect synthetic data. (b) Forecast: for every supported Listing the low, base and high scenarios are reproducible from documented parameters and ordered low ≤ base ≤ high. (c) Presentation: no UI string or API label contains "demo", and each estimate or forecast display carries exactly one limitation affordance (FR-47). (d) Unsupported cases return the unavailable state (FR-46). Validates FR-42 – FR-47. `[ASSUMPTION]`
- **SM-5: Quota correctness.** In a concurrency test of 20 parallel Publication requests against one remaining slot, exactly one succeeds; repeated retries never double-charge. A further test shows that republishing an archived Listing after changing an identity field consumes a slot, while unchanged same-month republication does not; a locality or area correction within 24 hours of Publication keeps its slot once and a second correction does not. Validates FR-17, FR-18, FR-20, FR-75.
- **SM-6: Privacy.** An automated scan of every public and other-account-facing response finds 0 seeded canary phone numbers and 0 seeded canary Private addresses (in format variants, seeded in stored account fields and Private addresses, not in Message bodies); authorisation tests cover every endpoint and the FR-74/FR-75 matrix (table-driven); seeded canary phone, email and address values that conform to the shared patterns do not appear in recorded AI-provider requests, and a false-positive set shows that prices, areas and counts are preserved. Validates FR-13, FR-21, FR-32, FR-51, FR-52.

**Secondary**
- **SM-7: Chat latency.** p95 time from send to delivery to an online recipient < 1 second in the local environment. Validates FR-49. `[ASSUMPTION]`
- **SM-8: Resilience.** With AI services (LLM, embedding and speech providers) disabled and the valuation model unaffected, 100% of manual creation, conventional search and messaging tests pass. Validates FR-31, FR-34, FR-38.
- **SM-9: Usability.** SUS score ≥ 68 from at least 8 test participants. Validates §4 as a whole. `[ASSUMPTION: requires recruiting participants]`
- **SM-10: AI benefit.** The developer times, with a stopwatch, a publishable Draft for the same 5 scripted Listings once with the AI Listing Assistant and once with the manual form, and the median of each is reported; in the SUS sessions, natural-language search task success is compared with conventional filters. Reported, with no pass threshold if participants are few. Validates FR-24 – FR-32 and FR-35 – FR-37. `[ASSUMPTION]`
- **SM-11: Retrieval quality.** On 40 domain questions the correct passage is in the top 3 retrieved for at least 32 (80%), measured with the project's local embedding model. Validates FR-28, FR-78. `[ASSUMPTION]`

**Counter-metrics (do not optimise)**
- **SM-C1:** Extraction rate must not be raised by auto-accepting unconfirmed values. Counterbalances SM-2.
- **SM-C2:** Do not tune the synthetic data generator or evaluation split to flatter MAPE. Counterbalances SM-4.
- **SM-C3:** Do not raise search recall by treating unknown attributes as matches. Counterbalances SM-3.
- **SM-C4:** Do not trade away participant-only access checks for chat speed. Counterbalances SM-7.
- **SM-C5:** Do not improve the AI-versus-manual time (SM-10) by skipping required confirmations. Counterbalances SM-10.

---

## 8. Cross-Cutting Requirements

### 8.1 Non-Functional Requirements
Thresholds are provisional `[ASSUMPTION]` for the local environment (about 10,000 synthetic Listings without images for performance testing, distinct from the sample marketplace data of FR-72 and the valuation training data of FR-77; up to 20 concurrent users).
- **NFR-1 Performance.** Conventional search p95 < 500 ms server time; Listing detail p95 < 300 ms; AI Price Estimate p95 < 1 s; Message delivery to an online recipient p95 < 1 s (SM-7); natural-language or voice search p90 ≤ 10 s end to end with a visible progress indicator; AI Listing Assistant turn p90 ≤ 15 s.
- **NFR-2 Security.** Authentication and authorisation enforced server-side; protected against common web attacks (cross-site scripting, request forgery, injection) and checked against an OWASP ASVS Level 1 subset `[ASSUMPTION]`; uploads validated (FR-15); abuse limits per NFR-10; secrets kept out of source control.
- **NFR-3 Privacy.** Phone, email and Private address minimised everywhere, kept out of AI provider requests by redaction (FR-32, residual risk disclosed), never in public responses or machine-learning inputs; verified by SM-6.
- **NFR-4 Reliability.** AI or retrieval failure degrades to manual and conventional paths (FR-31); publication and quota accounting are atomic and idempotent (FR-18); no state change is half-applied, verified by fault-injection tests (for example interrupting a Publication mid-transaction).
- **NFR-5 Data integrity.** One canonical Listing data model serves creation, search, AI assistance and valuation, with a single definition of field meanings (SYS-033; verified by the validation and authorisation tests of FR-21 and FR-22); unknown is never recorded as zero or false.
- **NFR-6 Accessibility and usability.** Responsive web (desktop and mobile browsers); search, Listing creation and chat usable by keyboard and meeting WCAG 2.1 AA for those flows `[ASSUMPTION]`; clear validation messages; English only.
- **NFR-7 Maintainability and testability.** Automated tests for domain rules, authorisation matrix, concurrency and AI contract validation; one documented contract between frontend and backend; reproducible local setup.
- **NFR-8 Observability.** Operational logging and AI failure counts; a log scan (as in SM-6) finds no seeded canary private values.
- **NFR-9 Compatibility.** Current versions of Chrome, Edge and Firefox on desktop and Android, and Safari on iOS on a best-effort basis; voice search depends on browser support and degrades to text.

- **NFR-10 Abuse limits.** All configurable, defaults `[ASSUMPTION]`: registration 10 per hour per client; verification and reset emails 5 per hour per account; Messages 60 per minute per account; new Conversations 20 per day per account; favourites 500 per account; Reports 10 per day and support requests 5 per day per account; 20 open Drafts per account; Assistant messages 30 per hour per account; natural-language and voice searches 10 per minute per Visitor and 30 per hour per signed-in account, with at least half of AI capacity reserved for signed-in Listing creation; estimate requests per FR-42; sign-in per FR-3. A "client" is identified by network address (behind a trusted proxy, using forwarded headers) and a session cookie, on a best-effort basis, so shared addresses can be throttled together. Extra accounts can be used to evade Publication quotas; this is accepted for the prototype and limited by email verification and these limits **[B23]**.

### 8.2 Constraints and Guardrails
- **Cost:** Hosted AI only on free tiers where possible; limits are unreliable and must not be assumed unlimited; no paid infrastructure required.
- **AI safety:** AI output is untrusted input; never writes data or executes queries by itself; never publishes; never claims legal verification.
- **Privacy and consent:** AI features send content to an external service; users are informed `[ASSUMPTION]`; provider terms on use of free-tier inputs must be checked when a provider is chosen.
- **Delivery:** sole developer working in small, testable, sequential tasks; sequencing by dependency (§6), not by deadline.
- **Platform:** web only; separate frontend and backend repositories.

### 8.3 Data retention (provisional) `[ASSUMPTION]`
| Data | Retention |
|---|---|
| Deleted Draft (data, images, Assistant history, Draft estimate snapshots) | Removed immediately; stored files within 24 hours |
| Abandoned Draft | Notified after 180 days without edits; deleted 30 days later unless opened (FR-16) |
| Assistant session history (redacted text) of a Published listing | 90 days after Publication |
| AI request log; ad-hoc estimate requests | 90 days; stored estimates of Published listings are kept with the Listing |
| Reports and evidence snapshots | 24 months after resolution; Message snapshots are anonymised when a participant account is deactivated; open Reports are kept until resolved |
| Verification requests and evidence | 24 months after the decision; deleted on account deactivation |
| Support requests | 12 months after closure; open requests are kept until closed |
| Notifications | 90 days after being read |
| Publication and quota events (with identity fingerprint); moderation action records | 24 months |
| Idempotency records | 7 days |
| Favourites | Until removed or the account is deactivated |
| View counters | Kept as per-Listing totals; no per-viewer data |
| Audit records | 24 months; immutable within the product, purged only by an operator retention job (account deletion never cascades into them) |
| Application logs | 30 days |
| Messages | Kept indefinitely in v1 **[DEFAULT R11]**; a Deactivated sender appears as "deleted user"; contact details users shared voluntarily remain in the counterpart's history |
| Orphaned uploads | Removed within 24 hours |

---

## 9. Open Questions
1. **OQ-1 Nearby-facility catalogue and free text.** Which facility types are supported beyond mosque, park, gym, school and hospital, and how are unmatched free-text mentions handled (assumed: flagged, not stored)? Proposed default: the catalogue is limited to the five named types.
2. **OQ-2 Karachi locality seed.** Which Karachi localities and societies are in the initial reference data and the synthetic valuation coverage (examples: Gulshan-e-Iqbal, DHA Phase 6, Clifton)?
3. **OQ-3 Agency verification evidence.** What business information or documents must an Agency submit for Verification? Proposed default: business name, registration or licence number and one supporting document.

## 10. Provisional Decisions (DEFAULT, awaiting confirmation)
Not promoted to confirmed requirements. Each is built into the FRs above only as tagged.

| ID | Provisional decision | Where it affects this PRD |
|---|---|---|
| R4 | Nearby facilities are catalogue entries with explicit present / none / unknown states, presence-only | FR-14, FR-26 |
| R5 | Categories, Subtypes, purposes, area units are product-defined and view-only for Administrators | FR-67 (amends ADM-045/047/048) |
| R7 | Images are validated server-side and location metadata stripped | FR-15 |
| R8 | Quota calendar-month in Karachi time; same-month republish free; restoration and edits free | FR-18 |
| R9 | Email verification required before publishing or messaging | FR-4, FR-17 |
| R10 | One generalised Report; Administrators see Messages only via Report; phone-pattern check on Listing text, not chat | FR-23, FR-54 |
| R11 | Deletion = admin-reviewed deactivation with anonymisation | FR-8 |
| R13 | Estimates for sale Listings only; unsupported categories or localities "unavailable"; scenario-range forecast | FR-45, FR-46 |
| R14 | Mechanisms for rule-based duplicate flags, the view counter, AI feature disabling and the Administrator second factor (time-based code); all in scope | FR-10, FR-56, FR-66, FR-68 |
| R3 | Field registry as a versioned file; installment, legal, video and floor-plan attributes deferred to P8 at the latest | FR-12 |
| R15 | Shared Agency account; no employee seats | FR-2, §2.2, §5 |
| R17 | In-app notification list and local mail capture | FR-76 |
| R1, R2, R6, R12 | Session, chat, API-contract and address-storage mechanisms | Addendum §1 only |
| R16 | Implementation phases P1–P8 ordered by dependency (replaces tiers) | §6, all FR tags |

**Resolved after validation (CONFIRMED, register B17–B25):** quota identity rule (FR-18, FR-75); one lifecycle and visibility table (FR-75); one account-state model (FR-74); form-only private data with redaction before AI providers (FR-32); single Administrator role with reasoned, audited private-data access (FR-9, FR-54, FR-61, FR-63, FR-69); estimate bound to the published revision (FR-42, FR-44). Table contents beyond the stated rules were drafted from these decisions and may be refined. B23: phone numbers are not unique; B24: one-time locality and area correction within 24 hours of Publication (FR-18); B25: Conversation writability and header rules (FR-48).

**R14 and B11:** register R14 called the Administrator second factor a stretch; B11 (all agreed requirements in scope) overrides it, so FR-10 stays in scope as one of the last items built.

**Deviations from the approved proposal:** chat uses Django Channels instead of Supabase Realtime; authentication is Django-managed instead of Supabase Auth; the Gantt chart's stack (Angular, AWS/Azure) is ignored (register B1, A2, A3); LangChain is not mandated, and the provider-independent AI interface may or may not use it (architecture).

**Amendments to source wording (applied in this PRD, flagged until confirmed):** USR-023/USR-071/AGN-066 ("historical data" → documented synthetic assumptions with a concise limitation note); SYS-011 and the privacy rule (city and locality public to Visitors); ADM-045/047/048 (view-only); ADM-001–003 (provisioned only); USR-017 (seller-declared nearby facilities); R9 (email verification before publishing and messaging, affecting USR-042, SYS-013, AGN-031, USR-087, USR-088, AGN-069, AGN-070); B17 and B24 (identity fields locked while published, affecting USR-077 and AGN-035).

## 11. Assumptions Index
*Every inline `[ASSUMPTION]`, surfaced for explicit confirmation:*
- §4.1 FR-1 — phone format validation scope; identical registration response for new and existing emails.
- §4.1 FR-1 — password minimum policy (8 characters, not common).
- §4.1 FR-3 — sign-in throttle (5 failures per 15 minutes).
- §4.1 FR-4 — verification link validity (24 hours).
- §4.1 FR-5 — password change ends other sessions.
- §4.1 FR-2 — Agency full office address private.
- §4.1 FR-6 — email change keeps the old address valid until the new one is verified.
- §4.1 FR-9 — Administrator session limits (30 minutes idle, 8 hours total); password reset through the provisioning mechanism.
- §4.2 FR-15 — image formats, size (5 MB), count (10), pixel limit (8000 x 8000) and per-account storage (200 MB Individual, 1 GB Agency).
- §4.2 FR-23 — pattern-based, best-effort contact and address detection.
- §4.3 FR-26 — unmatched nearby-facility mentions flagged, not stored (see OQ-1).
- §4.3 FR-28 — retrieval hit rate of at least 80% in the top 3.
- §4.3 FR-31 — AI call timeouts (12 seconds for search, 20 seconds for an Assistant turn).
- §4.3 FR-32 — input length caps (2,000 and 500 characters).
- §4.3 FR-78 — knowledge base of at least 100 passages.
- §4.3 FR-32 — notice that AI uses an external service; pattern-based redaction is best-effort for free-text addresses.
- §4.1 FR-74 — reactivation of Deactivated accounts is not supported.
- §4.5 FR-42 — estimate request throttle (20 per hour per account).
- §4.6 FR-54 — context of up to 5 preceding Messages shown with a Report.
- §4.4 FR-37 — audio not retained after transcription.
- §4.4 FR-38 — Visitors identified by network address and session cookie.
- §4.6 FR-48 — Message length limit (2,000 characters).
- §4.7 FR-56 — view-count de-duplication window (30 minutes).
- §4.5 FR-45 — low/base/high scenario range for the forecast.
- §4.5 FR-44 — "about equal" band of ±5%.
- §4.5 FR-46 — supported scope for estimates (sale; House, Apartment, Plot; covered Karachi localities).
- §4.8 FR-64 — controlled report reasons list (including AI-generated content).
- §4.8 FR-66 — suspicious flag after two open Reports within 7 days.
- §4.1 FR-5 — reset link expiry (1 hour).
- §4.2 FR-13 — society, phase, sector and block are public.
- §4.2 FR-16 — Draft abandonment (180 days plus 30 days notice).
- §4.4 FR-36 — rule for required versus preference criteria.
- §4.8 FR-59 — suspension is the only restriction in v1.
- §4.9 FR-79 — second-person label review where available.
- §4.9 FR-73 — local setup within one hour.
- §7 — all Success Metric thresholds (SM-2, SM-3, SM-4, SM-7, SM-9).
- §8.1 — performance and scale thresholds, local-environment sizing, OWASP ASVS Level 1 subset, WCAG 2.1 AA for main flows and NFR-10 abuse limits.
- §8.3 — all retention periods.
- §8.2 — AI-use disclosure to users.
