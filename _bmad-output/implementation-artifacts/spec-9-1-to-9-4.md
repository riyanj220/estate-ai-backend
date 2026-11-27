# Stories 9.1 to 9.4: Reports and Listing moderation (BE+FE)

Acceptance criteria: `epics.md`, Epic 9. Rules: FR-63, FR-64, FR-65, FR-75, AD-5, AD-7. Owner direction: a final-year
project; keep governance simple (one administrator role, no appeals, no multi-level workflow).

## API

| Route | Meaning |
|---|---|
| `POST /api/v1/listings/{id}/reports` | `reason`, `description?`; 403 own or Suspended, 404 not public, 409 already open, 429 after 10 a day |
| `GET /api/v1/me/reports/{id}` | the reporter's own Report; anyone else 404 |
| `GET /api/v1/admin/reports?status&reason&cursor` | queue, oldest first, with unresolved counts |
| `GET .../reports/{id}`, `POST .../{id}/start`, `/resolve`, `/dismiss` | detail and review; `notes` required to close |
| `GET /api/v1/admin/listings?owner&category&status&created_from&created_to&cursor` | review list; Drafts only with `status=draft`, without content |
| `GET .../listings/{id}`, `POST .../{id}/read` `{reason}` | non-Draft detail; reasoned Draft read |
| `POST .../{id}/restrict`, `/lift-restriction`, `/remove`, `/restore`, `/archive` `{reason}` | moderation |

The owner resource gains `under_review`.

## Decisions

- `governance` owns `Report`, append-only `ReportTransition` and append-only `ModerationAction`. `Report` has
  `target_type` (`listing` or `conversation`), `target_id`, `subject_account_id` and a JSON `snapshot`, so 9.5 adds
  Conversation Reports without a reshape. 9.6 flags are advisory and get their own table; the index on target and
  status serves the two-reporters rule.
- A Listing Report snapshot is the public view only (no advertiser contact, owner or Private address), so reading it
  needs no reason. The report queue and detail serve `listing` Reports only until 9.5 adds the reasoned read.
- Strict path: pending, reviewing, resolved or dismissed. Notes are administrator only; the reporter's notification
  says only the outcome.
- `under_review` is composed in presentation; `listings` never reads governance data.
- Actions by status (`listings.lifecycle.moderation_actions`): published: restrict or lift, remove; archived:
  restrict or lift; removed: restore, archive, lift if restricted; Draft, sold, rented: none.
- Writers: `listings.moderation` (restrict, lift, remove, archive), `publication.restore_after_moderation` (restore:
  one uncounted `restore` event, no quota, no `listing.published`). Guards extended: single status writer, `removed`
  naming, moderation columns, visibility allow-list.
- New column `removal_reason`, so a restriction keeps its own reason across removal and restoration.
- Moderation reason: 5 to 120 characters, no contact details, so it fits in the owner's notification.
- Administrators get non-Draft images through the one media endpoint (`policy.admin_may_inspect`); `can_view` is
  unchanged.

## Departures

- Draft images are counted, not shown, to administrators; administrators never see the Private address here.
- The owner filter takes an account id (each row offers "This owner").
- No reporter-facing Report page in the frontend (the API exists); outcomes arrive as notifications.
- Deactivation does not yet close Reports (Epic 10). UI polish deferred.

## Operating note

After pulling: `uv run python manage.py migrate --database=migrator`, then
`uv run python manage.py grant_runtime_privileges` (two new append-only tables).

## Review

Moderation security review: no medium-or-higher findings. Departure from FR-75 (from Story 2.17): the owner of a restricted Listing cannot edit it or mark it sold or rented. Accepted: report evidence keeps only the image count; outcome notifications do not name the report; restore does not check the owner state (hidden while not Active; deactivation is Epic 10).
