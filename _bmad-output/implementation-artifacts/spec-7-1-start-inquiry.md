# Story 7.1: Start an Inquiry (BE+FE)

Acceptance criteria: `epics.md`, Epic 7. Rules: FR-48, FR-51..FR-53, AD-5, AD-13, AD-16, AD-18.

## Data (`apps/messaging`)

- `Conversation`: `listing_id` (plain UUID, history outlives the Listing), `initiator_account_id`,
  `owner_account_id`, `last_seq`, `last_message_at`, `initiator_read_seq`, `owner_read_seq`
  (unread state for 7.5). Unique per (listing, initiator).
- `Message`: conversation, `seq`, `sender_account_id`, `body`, `client_message_id`, `created_at`.
  Unique (conversation, seq) and (conversation, sender, client_message_id).

## API

| Route | Meaning |
|---|---|
| `GET /api/v1/conversations?listing_id=` | the caller's own inquiry on a Listing (0 or 1) |
| `POST /api/v1/conversations` `{listing_id, body}` | start, or continue the existing one |
| `GET /api/v1/conversations/{id}` | header, counterpart name, writability |
| `GET /api/v1/conversations/{id}/messages?after=` | Messages after a `seq`, in order |
| `POST /api/v1/conversations/{id}/messages` `{body}` | send (either participant) |

Both POSTs need `Idempotency-Key` (the client message id).

## Decisions

- A Conversation is writable while its Listing is publicly visible and the counterpart is not
  Deactivated. `POST /conversations` on an existing read-only thread answers 200 with
  `message: null` (nothing is saved); `POST .../messages` is refused 409.
- Header: the title only while publicly visible, else `sold`, `rented` or `unavailable`.
- Participants see each other's display name (agency name or full name; "Deleted user" when
  Deactivated). No account id, email or phone is in any payload.
- Anyone else, administrators included, gets `not_found`.
- `messaging.message_sent` (ids only) is emitted after commit; 7.3 subscribes.
- Limits: `message_send` 60/minute, `conversation_new` 20/day, per account. Replays do not count.
- Body: 1 to 2,000 characters after trimming, line endings normalised; not contact-scanned,
  never logged.
- Frontend: "Message seller" on the Listing page, a Conversation page at `/messages/[id]` with a
  reply box. No inbox, polling or WebSocket (7.2 to 7.5).
