# Epic 1: accepted deviations from the written contract

Recorded by the Epic 1 retrospective so later runs stop re-flagging them. Each is a deliberate, reviewed decision; the story spec's Review Triage Log holds the evidence.

| Story | Written contract | As built | Why |
|-------|------------------|----------|-----|
| 1.17 | 5 failed sign-ins in 15 minutes for one account and address are throttled | Keyed by (email, client network); administrators also get a per-account ceiling (Story 1.22). Locked-out attempts return the same generic 401, never 429. | A distinct lockout answer would reveal that an account exists. |
| 1.18 | More than 5 reset emails per hour per account returns the 429 envelope | The 429 is per client; the per-account cap is silent and the response stays identical. | A per-account 429 reveals existence. |
| 1.19 | Agency registration "applies the same rate limits" | Shares the `registration` scope with Individuals. | One abuse budget per client. |
| 1.20 | `pending_email` shows an unconfirmed change | Shown for every accepted request regardless of whether a link was sent. | Showing it only for issued links was an enumeration oracle. |
| 1.21 | Contact-detail check rejects phone numbers and addresses | Best-effort: the shared patterns miss obfuscated forms (`docs/privacy-patterns.md`). | Hardening is a separate story. |
| 1.22 | Failed and successful administrator sign-ins are recorded | Failures are audited only for an existing active administrator and only below the per-account ceiling; no operator identity. | Keeps the audit trail from probing addresses or flooding. |
| 1.22 | Administrators sign in like other accounts | Own door `/api/v1/admin-session`; the ordinary door refuses them; sessions share the `estate_session` cookie. | Short lifetimes and a separate throttle; the shared cookie is a recorded follow-up. |
| 1.23 | Redirect to "the admin sign-in page" | Visitor, non-administrator and expired administrator are indistinguishable; the shared `/account/password` page shows the admin chrome. | No confirmation of the admin area to non-administrators. |
| 1.24 | Notifications newest first with cursor pagination | A late-committing notification can carry a lower sequence number than one already visible; clients refetch page 1 and use the unread count. | The cursor carries a global position; acceptable for volume-only leakage. |
