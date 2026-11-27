---
title: 'Story 11.1: full sample marketplace'
status: 'review'
---

## Intent

FR-72: one command that loads a realistic Karachi marketplace, so every screen can be checked against the real backend.
Repeatable, removable, no quota, no AI provider or network call, no sample banner. Backend only.

## What was built

- `apps/listings/sample_data.py`: 14 accounts, a 40-row Listing table (default 40, up to 120), installment and legal
  fields, floor plans, tour links.
- `apps/governance/sample_data.py`: everything around them (statuses, Drafts, favourites, views, Conversations, Reports,
  flags, Verification, support and closure requests, suspension, notifications) and the removal of all of it.
- `apps/kernel/sample_art.py`: drawn scenes, floor plans and logos (Pillow stays inside `kernel`).
- Small removal services: `messaging/sample_data.py`, `listings.services.remove_sample_favourites`,
  `governance.flags.check_suspicious`.
- The command moved to `apps/governance/management/commands/`; name and flags unchanged. Docs: `docs/sample-data.md`,
  `docs/commands.md` section 4, `docs/testing.md`, README.

## Decisions

- Composition lives in `governance`, the highest domain layer; no new app and no import-linter change.
- Realistic but invented names replace the "Sample" names; records are identified by the reserved email domain only.
- Administrator decisions are made with an administrator id that no account has. No administrator is created.
- One account is suspended, one has a pending closure request, one is completely empty.
- Removal also deletes Conversations and Reports between a sample and a non-sample account, and records made by hand
  while signed in as a sample account.
- Append-only histories of sample records are deleted through the privileged connection, as the seed events already are.
- Activity is made once (marked by a sample Verification request); an interrupted run is fixed with `--reseed`.
- Pictures are 800x600 drawings, 2 to 5 per Listing. Default Listing count 40 (was 12), maximum 120 (was 60).
- The tour links use the reserved `tours.example.com`.

## Checklist (docs/story-checklist.md)

No route, event, mail, migration, account-existence, email-token or admin-guard surface is added.

## Left out

- Dates are not spread into the past: Listings, Messages and requests carry the time of the run.
- No Assistant sessions, search history or voice data (they would need a provider call).
- Not checked in the frontend.
