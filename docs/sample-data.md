# Sample data (Story 2.16)

A developer tool that fills a local database with realistic accounts and published Listings, so the frontend and later
stories have something to show and test against. Backend only; later epics extend the table in
`apps/listings/sample_data.py`.

```
python manage.py seed_sample_data [--reset] [--reseed] [--yes] [--password-stdin] [--listings N]
```

- no flag: create what is missing. A second run creates and changes nothing; `--listings 20` after `--listings 12` adds
  only the missing eight. `N` is 1 to 60 (default 12).
- `--reset`: remove every record the command created, and nothing else.
- `--reseed`: `--reset`, then create.
- `--yes`: required with `--reset` and `--reseed`, which first print `will remove: N accounts, N listings, N publication events`.
- `--password-stdin`: read the password of the sample accounts from one line of standard input.

## What it creates

- 3 Individual and 2 Agency accounts, Active and email-verified, profiles filled in (a full name, or an agency name,
  description and Karachi), through `identity.services.create_sample_account` as the system actor. No mail is sent. The
  phone (`+920000000000`) and office address are obviously fake.
- About 12 Listings (`--listings`), each created as a Draft with `apply_updates` (origin `manual`), given 1 to 3 images
  through `upload_image` as the owner (small valid PNGs generated in code, stored through `kernel.media`, so usage and
  files are real) and published with `publication.publish(kind="seed")` as the system actor. They cover sale and rent,
  House, Apartment, Residential and Commercial Plot, Shop and Office, Karachi and its localities, amenities and nearby
  facilities, and a private street and house number on some (so private-field tests have data). Listing `i` belongs to
  account `i mod 5`. Prices, areas and units are valid under the registry. The data is a fixed table, so every run makes
  the same titles; Listings past the twelfth repeat the table with a "(no. n)" title and a deterministic price change.
- No quota is used: seed events are `counted=False`, so sample accounts keep their full monthly allowance
  (`docs/listings.md`, "The `seed` kind").

## How records are identified

Every sample account has an address on `sample.estate-ai.invalid` (`.invalid` is reserved by RFC 2606 and cannot
receive mail). The match is the whole domain; an address that merely contains it, or an administrator on it, is never
a sample account (an ASCII address that, lowercased, ends with `@sample.estate-ai.invalid`; the Python and SQL matchers share `identity/sample_domain.py`). Registration, email change, password reset and verification requests, `create_account` and `provision_admin` refuse that domain and any subdomain (`invalid_email`); only the system-only sample service bypasses it. Every sample Listing belongs to a sample account. `--reset` removes, in this order: the Listings of
those accounts (images, usage and files through `kernel.media`; audit rows are kept, as audit, and each Listing writes `listing.sample_removed`, never `listing.draft_deleted`), their `seed` publication
events (below), then the accounts (profile, sessions and tokens cascade; the empty storage-usage row goes too). `remove_sample_account` on its own refuses (False, nothing changed) while the account still owns stored media. `services.remove_sample_listing` and
`services.remove_sample_account` refuse anything not on the reserved domain, so even a mistaken call cannot touch a real
record. Reset leaves any non-sample account or Listing alone, including ones that look similar.

Publication events: the runtime role may only INSERT and SELECT them, so `--reset` deletes the seed events through the
privileged connection alias (`migrator` in local development; the migrator password is in `.env`). Seed idempotency keys
are `seed-<index>` per account and a reseed makes new accounts, so keys never clash.

## Passwords

There is no default password and none in the repository. When an account has to be created: `--password-stdin` reads one
line; on a terminal a hidden prompt asks twice (empty means generate one); otherwise a random password is generated and
printed once, at once when the accounts exist (`seed accounts password: ...`, so a later failure cannot lose it; after a failure the command adds "run with --reseed to start over"), never logged. A run that creates no account needs and
prints no password. An existing sample account keeps its password (a rerun that was given a password prints one notice line); `--reseed` is the way to change it. The password
must pass the ordinary password policy.

## Safety

- Refuses under the production settings (`config.settings.prod`, whatever `SEED_ALLOWED` says) with one line and a
  non-zero exit, and under any module other than `config.settings.local` and `config.settings.test` unless
  `SEED_ALLOWED=true` is set in the environment. `config/settings/prod.py` pins `SEED_ALLOWED = False`.
- The default database host must also be loopback (`127.0.0.1`, `localhost`, `::1`, empty or a unix socket) unless `SEED_ALLOWED=true`.
- Not part of any migration or start-up path (a test checks this).
- Output is plain counts. It never prints an address, name, private value or password (apart from the one generated
  password line), and nothing is logged.

## How tests use it

Call `sample_data.seed(count, password)`, `reset()` and `reseed()` directly (no CLI); `tests/listings/test_sample_data.py`
is the model and needs `seed_cap()` and `seed_media_policies()` from `tests/listings/support.py`. A 12-Listing run takes
about 3 seconds; prefer `seed(5)`. The reset-and-reseed behaviour, the system-only guard and the production refusal are
all tested there.
