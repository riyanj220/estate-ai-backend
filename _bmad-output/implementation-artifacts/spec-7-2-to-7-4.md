# Stories 7.2 to 7.4: live delivery

Acceptance criteria: `epics.md`, Epic 7. Rules: FR-49, FR-51, AD-13, AD-14, AD-18, AD-19.

## Contract

`GET /ws` (WebSocket upgrade), authenticated by the session cookie. Server to client only.
Shapes: `events.schema.json` (`manage.py events_schema --file events.schema.json`; a test fails when stale).

| Event | Meaning |
|---|---|
| `ready` | events flow from now; read what came before over REST |
| `message.created` `{conversation_id, message_id, seq}` | fetch `.../messages?after=<seq held>` |

| Case | Result |
|---|---|
| Origin missing or not exactly a trusted origin (scheme, host, port) | handshake refused |
| Visitor, ended or expired session, administrator, Suspended, Deactivated | handshake refused |
| Path other than `/ws` | refused; a client cannot name a room |
| Sixth socket of an account | closed 4429 |
| Any client frame (1 KiB at most) | closed 4403 |
| No event for 15 minutes | closed 4408; reconnect |
| Sign-out, password change, deactivation, suspension | that session's sockets closed 4401 |
| Message saved | after commit, the two participants of the Conversation row only |
| Retry with the same key | nothing stored or announced again |

## Decisions

- Events carry ids and numbers only. REST stays the one place Conversation content is read.
- `channels` with the in-memory layer: one server process only. No Redis.
- Session and account are re-checked before each event and every 30 s (covers changes made by another
  process, which cannot signal this one).
- New `account.suspended` domain event (ids only) closes sockets at once.
- Email-unverified accounts may hold a socket (they may read Conversations); Suspended may not.
- `/ws` is on the site's own origin: the Next.js server proxies the upgrade (dev and production), so no
  public backend URL and no second Origin list are needed. AD "the WebSocket connects directly" is
  superseded by this, in line with AD-13's deployment note.
- `daphne` is in the local `INSTALLED_APPS`, so `manage.py runserver` serves `/ws`.
- Send, sign-in and session views stay synchronous and database-only (`docs/architecture-notes.md`).
- Frontend: `useLiveEvents` on the Conversation page; reconnects with backoff, resumes from the last
  contiguous `seq`; "Check for new messages" stays as the fallback. UI polish deferred.
- Latency (7.4): `tests/perf/test_chat_latency.py` (marker `perf`); numbers in `docs/performance.md`.

Out of scope: inbox, unread state (7.5), restrictions (7.6), anonymisation (not built; the periodic
check covers it when it is).
