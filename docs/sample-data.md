# Sample data (Stories 2.16 and 11.1)

A developer tool that fills a local database with a whole Karachi marketplace, so that every screen has realistic,
non-empty data to show and test against. Backend only. No screen says the data is sample data.

```
python manage.py seed_sample_data [--reset] [--reseed] [--yes] [--password-stdin] [--listings N]
```

- no flag: create what is missing. A second run creates and changes nothing. `--listings 60` after 40 adds the missing 20.
- `--listings N`: 1 to 120, default 40. Fewer than 40 leaves some screens thin.
- `--reset`: remove every record the command created, and nothing else.
- `--reseed`: `--reset`, then create. Use it to move from an older set of sample data.
- `--yes`: required with `--reset` and `--reseed`, which first print what they will remove.
- `--password-stdin`: read the password of the sample accounts from one line of standard input.

A default run takes about a minute on a laptop (password hashing and drawing the pictures are most of it).
Run `register_valuation_model` first, otherwise no price estimates are stored during the run. Nothing calls an AI provider
or the network; estimates are computed by the local model. No publication quota is used and no mail is sent.

## Accounts

All are `<name>@sample.estate-ai.invalid` with one shared password.

| Address starts | Name | What it is for |
| --- | --- | --- |
| `sample.agency.1` | Harbour Gate Estates | Verified. Most Listings, busy inbox with unread, Drafts, a restricted and a removed Listing, a sold one, notifications |
| `sample.agency.2` | Indus Key Realty | Verified. Family homes; has a rented and a reported Listing |
| `sample.agency.3` | Margalla Square Properties | Verification pending; plots and files |
| `sample.agency.4` | Seaview Property Partners | Verification rejected; offices and shops |
| `sample.agency.5` | Crescent Homes Karachi | Verification revoked |
| `sample.agency.6` | Blue Minar Associates | Never asked for Verification, no logo, no description |
| `sample.individual.1` | Sana Qureshi | Main buyer: 12 favourites (some no longer available), unread Messages, Reports, support requests, Drafts |
| `sample.individual.2` to `.5` | Bilal, Hina, Usman, Ayesha | Other sellers and buyers |
| `sample.individual.6` | Farhan Sheikh | Pending account closure request |
| `sample.individual.7` | Nadia Hussain | Suspended (her Listing is hidden) |
| `sample.individual.8` | Omar Farooq | Completely empty, for the empty screens |

## What it creates

- **Listings** (40 by default): sale and rent across House, Apartment, Residential and Commercial Plot, Plot File, Shop and
  Office, in many Karachi localities and societies, with amenities, seller-declared nearby facilities, private addresses
  on some, installment and legal declarations, 2 to 5 photographs each, a floor plan on some and a tour link
  (`https://tours.example.com/...`, which opens nothing) on some. Sale prices follow the invented locality rates of the
  price model, so estimates sit near them. Past the fortieth the table repeats with "(no. n)" titles and other sizes.
- **Statuses**: Published, Draft (complete, partial and untitled), Archived, Sold, Rented, Removed and restricted.
- **Activity**: logos and profile pictures; Verification requests (verified, pending, rejected, revoked); view counts;
  favourites; 12 Conversations, read and unread, some read-only because the Listing was let or the account suspended;
  Listing and Conversation Reports (pending, reviewing, resolved, dismissed); a duplicate and a suspicious flag; support
  requests (open and closed); one closure request; one suspended account; read and unread notifications.

Everything made at one run carries that run's time (dates are not spread into the past). Sample notifications and the
moderation history name an administrator id that has no account, so no administrator can sign in with it. Create your
own with `provision_admin` to see the admin screens.

The photographs are free Unsplash files in `apps/kernel/sample_art/photos/` (sources in its `CREDITS.md`); floor plans,
agency logos and profile pictures are drawn by `apps/kernel/sample_art/` (only `kernel` may use Pillow).

## How it is built

`apps/listings/sample_data.py` makes the accounts and published Listings (`publication.publish(kind="seed")`).
`apps/governance/sample_data.py` calls it and adds the activity; it is in `governance`, the highest domain layer, because
it also needs `messaging`. The command is `apps/governance/management/commands/seed_sample_data.py`. Everything is
created through the owning apps' services.

The activity is made only when no sample Agency has a Verification request yet, so a second run does not repeat it. If a
run fails part way, start again with `--reseed --yes`.

## How records are identified and removed

Every sample account has an address on `sample.estate-ai.invalid` (`.invalid` is reserved by RFC 2606 and cannot receive
mail). The match is the whole domain; an address that merely contains it, or an administrator on it, is never a sample
account (`identity/sample_domain.py`). Registration, email change, password reset and verification requests,
`create_account` and `provision_admin` refuse that domain and any subdomain; only the system-only sample service bypasses
it.

`--reset` removes, in this order: Conversations a sample account takes part in; Reports made by or about a sample account;
moderation actions and flags of sample Listings; Verification, support and closure requests of sample accounts; their
favourites; their Listings with pictures and floor plans (each writes `listing.sample_removed`); their `seed` publication
events; the accounts (profile, picture, sessions, notifications). This includes records you made by hand while signed in
as a sample account, and Conversations or Reports between your own account and a sample one. Other accounts and their
records are untouched.

The runtime role cannot delete publication events or the Verification, Report and moderation histories, so `--reset`
deletes those through the privileged connection (`migrator` in local development; its password is in `.env`).

What stays: audit records, and short-lived technical rows (idempotency records, rate-limit counters, view buckets, the
AI request log of local estimates), which retention removes.

## Passwords

There is no default password and none in the repository. When an account has to be created: `--password-stdin` reads one
line; on a terminal a hidden prompt asks twice (empty means generate one); otherwise a random password is generated and
printed once, as soon as the accounts exist (`seed accounts password: ...`), never logged. A run that creates no account
needs and prints no password. An existing sample account keeps its password; `--reseed` is the way to change it.

## Safety

- Refuses under the production settings (`config.settings.prod`, whatever `SEED_ALLOWED` says) with one line and a
  non-zero exit, and under any module other than `config.settings.local` and `config.settings.test` unless
  `SEED_ALLOWED=true`. `config/settings/prod.py` pins `SEED_ALLOWED = False`.
- The default database host must also be loopback unless `SEED_ALLOWED=true`.
- Not part of any migration or start-up path (a test checks this).
- Output is plain counts. It never prints an address, name, private value or password (apart from the one generated
  password line), and nothing is logged.

## How tests use it

Call `listings.sample_data.seed(count, password)` for accounts and Listings only (about 0.3 seconds a Listing), or
`governance.sample_data.seed` for the whole marketplace. See `tests/listings/test_sample_data.py` and
`test_sample_marketplace.py`; they need `seed_cap()` and `seed_media_policies()` from `tests/listings/support.py`.
