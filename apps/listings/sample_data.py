"""Sample data for development and tests (Story 2.16). Never runs in production.

`seed()` creates, as the system actor and through the real services, 3 Individual and 2 Agency
accounts (Active, email-verified, on the reserved `sample.estate-ai.invalid` domain) and a number
of published Listings with images. Every Listing is created as a Draft with `apply_updates`,
gets its images through `upload_image` as the owner, and is published with
`publication.publish(kind="seed")`: no quota is used. `reset()` removes exactly those records;
`reseed()` is both. The data comes from a fixed table, so two runs make the same Listings.

The functions are the whole behaviour; `manage.py seed_sample_data` only parses arguments and
prints counts. Nothing here is imported by a migration, a start-up path or an HTTP route.
"""

import contextlib
import itertools
import os
import random
import secrets
import struct
import zlib
from dataclasses import dataclass, field
from functools import cache

from django.conf import settings

from apps.catalog import selectors as catalog
from apps.identity import passwords, services
from apps.identity import selectors as identity
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.listings import publication, selectors
from apps.listings import services as listings

DEFAULT_LISTINGS = 12
MAX_LISTINGS = 60
CITY_NAME = "Karachi"
LOCAL_MODULES = frozenset({"config.settings.local", "config.settings.test"})
PRODUCTION_MODULE = "config.settings.prod"
SYSTEM = Actor.system("sample-data")


class SeedRefused(Exception):
    """The command may not run here, or its arguments or reference data are wrong. The message is
    one plain line with no private value."""


# (kind, email local part, name, description). Obviously fake names; the address domain is
# `identity.SAMPLE_EMAIL_DOMAIN` and cannot receive mail.
ACCOUNTS = (
    ("individual", "sample.individual.1", "Sana Sample", ""),
    ("individual", "sample.individual.2", "Bilal Sample", ""),
    ("individual", "sample.individual.3", "Hina Sample", ""),
    ("agency", "sample.agency.1", "Sample Realty Group", "A fictional agency for sample data."),
    ("agency", "sample.agency.2", "Sample Homes Karachi", "A fictional agency for sample data."),
)

# One row per Listing, in order; Listing i belongs to ACCOUNTS[i % 5]. Prices are PKR text.
# `extra` holds registry field ids beyond the common ones; `amenities` are catalogue codes;
# `street` and `house_number` are private (kept on some Listings so private-field tests have data).
_ROWS = (
    {
        "kind": ("sale", "house", "house"),
        "title": "Spacious 500 sq yd bungalow with a garden",
        "text": "A well kept double storey bungalow with a lawn, a covered car porch and large "
        "bedrooms, close to parks and schools. Quiet street, ready to move in.",
        "locality": "DHA Phase 6",
        "price": ("12500000", "total"),
        "area": ("500", "sqyd"),
        "rooms": (5, 5),
        "extra": {"construction_year": 2018, "condition": "excellent", "total_floors": 2},
        "amenities": ("electricity_backup", "community_lawn", "nearby_park"),
        "private": ("Sample Street", "12-A"),
        "society": "DHA Phase 6",
        "block": "Block A",
    },
    {
        "kind": ("sale", "apartment", "apartment"),
        "title": "Sea facing 3 bedroom apartment in Clifton",
        "text": "Bright apartment on a high floor with a balcony, two parking spaces and 24 hour "
        "building security. Walking distance to shops and the promenade.",
        "locality": "Clifton",
        "price": ("32500000", "total"),
        "area": ("2100", "sqft"),
        "rooms": (3, 3),
        "extra": {
            "floor_number": 7,
            "building_floors": 12,
            "lift": True,
            "backup_electricity": True,
            "building_security": True,
        },
        "amenities": ("elevator", "intercom", "nearby_mosque"),
    },
    {
        "kind": ("sale", "plot", "residential_plot"),
        "title": "Residential plot of 10 marla in a gated society",
        "text": "Level corner plot in a developed block with paved roads, underground utilities "
        "and a boundary wall. Possession is ready and the file is complete.",
        "locality": "Bahria Town Karachi",
        "price": ("9500000", "total"),
        "area": ("10", "marla", "272.25"),
        "extra": {
            "possession_status": "possession_ready",
            "development_status": "developed",
            "corner_plot": True,
        },
        "amenities": (),
        "private": ("Sample Avenue", "44"),
    },
    {
        "kind": ("sale", "commercial", "shop"),
        "title": "Ground floor shop on a busy main road",
        "text": "Corner shop with a wide frontage and a shutter, on a road with constant foot "
        "traffic. Suitable for retail or a showroom.",
        "locality": "Saddar",
        "price": ("45000000", "total"),
        "area": ("450", "sqft"),
        "extra": {"business_use": "retail", "road_access": "main_road", "floor_number": 0},
        "amenities": ("atm_in_building",),
    },
    {
        "kind": ("rent", "house", "house"),
        "title": "Family house for rent near schools",
        "text": "Three storey house with a drawing room, a separate dining room and a servant "
        "quarter. Gas and water connections are in place; available for a family.",
        "locality": "Gulshan-e-Iqbal",
        "price": ("120000", "rental_period"),
        "period": "month",
        "area": ("240", "sqyd"),
        "rooms": (4, 3),
        "extra": {"security_deposit": "240000", "occupant_restrictions": "families_only"},
        "amenities": ("electricity_backup", "nearby_school"),
        "private": ("Sample Lane", "8"),
    },
    {
        "kind": ("rent", "apartment", "apartment"),
        "title": "Furnished 2 bedroom apartment for rent",
        "text": "Fully furnished apartment with air conditioning in both bedrooms, a modern "
        "kitchen and a lounge. Internet ready, lift and backup power in the building.",
        "locality": "Bahadurabad",
        "price": ("85000", "rental_period"),
        "period": "month",
        "area": ("1100", "sqft"),
        "rooms": (2, 2),
        "extra": {"furnishing": "furnished", "floor_number": 3, "building_floors": 6},
        "amenities": ("central_air_conditioning", "broadband_internet", "elevator"),
    },
    {
        "kind": ("rent", "commercial", "office"),
        "title": "Open plan office space for rent",
        "text": "Open plan office on the third floor with a reception area, a meeting room and "
        "a pantry. Central air conditioning and standby power included.",
        "locality": "PECHS",
        "price": ("150000", "rental_period"),
        "period": "month",
        "area": ("1800", "sqft"),
        "extra": {
            "business_use": "office_work",
            "floor_number": 3,
            "building_floors": 8,
            "lift": True,
        },
        "amenities": ("conference_room", "business_centre"),
        "private": ("Sample Boulevard", "3-B"),
    },
    {
        "kind": ("sale", "house", "house"),
        "title": "Renovated 240 sq yd house in North Nazimabad",
        "text": "Recently renovated house with new fittings, a modern kitchen and a roof "
        "terrace. Close to the main market and public transport.",
        "locality": "North Nazimabad",
        "price": ("38000000", "total"),
        "area": ("240", "sqyd"),
        "rooms": (4, 3),
        "extra": {"condition": "good", "parking_spaces": 2, "total_floors": 2},
        "amenities": ("waste_disposal", "nearby_hospital"),
    },
    {
        "kind": ("sale", "apartment", "apartment"),
        "title": "Affordable 2 bedroom flat near university road",
        "text": "Compact flat in a well managed building with a lift, a dedicated parking spot "
        "and a small balcony. A good first home or a rental investment.",
        "locality": "Gulistan-e-Jauhar",
        "price": ("11500000", "total"),
        "area": ("950", "sqft"),
        "rooms": (2, 2),
        "extra": {"floor_number": 2, "building_floors": 5, "balcony_or_terrace": True},
        "amenities": ("elevator", "nearby_school"),
    },
    {
        "kind": ("sale", "plot", "commercial_plot"),
        "title": "Commercial plot on a 120 foot wide road",
        "text": "Prime commercial plot with road access on two sides, suitable for a plaza or "
        "a showroom. Clear title and all development charges paid.",
        "locality": "Scheme 33",
        "price": ("60000000", "total"),
        "area": ("400", "sqyd"),
        "extra": {"possession_status": "possession_ready", "road_width_ft": "120"},
        "amenities": (),
    },
    {
        "kind": ("rent", "house", "house"),
        "title": "Compact 3 bedroom house for rent in Malir",
        "text": "Single storey house with a small courtyard, tiled floors and a covered parking "
        "space. Peaceful neighbourhood with shops and a mosque close by.",
        "locality": "Malir",
        "price": ("55000", "rental_period"),
        "period": "month",
        "area": ("120", "sqyd"),
        "rooms": (3, 2),
        "extra": {"occupant_restrictions": "no_restriction"},
        "amenities": ("nearby_mosque",),
        "private": ("Sample Road", "21"),
    },
    {
        "kind": ("rent", "commercial", "shop"),
        "title": "Shop for rent in a busy Korangi market",
        "text": "Small shop with a glass front in a crowded market, ready for a tailor, a "
        "pharmacy or a mobile accessories store. Electricity and water available.",
        "locality": "Korangi",
        "price": ("65000", "rental_period"),
        "period": "month",
        "area": ("300", "sqft"),
        "extra": {"business_use": "retail", "road_access": "main_road", "floor_number": 0},
        "amenities": (),
    },
)


@dataclass
class Report:
    """What a run did, as plain counts. `generated_password` is set only when a new account was
    created without a given password; the caller prints it once and never logs it."""

    accounts_created: int = 0
    accounts_existing: int = 0
    listings_created: int = 0
    images_created: int = 0
    accounts_removed: int = 0
    listings_removed: int = 0
    events_removed: int = 0
    generated_password: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:  # never the password, even in a debugger or a failed assertion
        return (
            f"Report(accounts_created={self.accounts_created}, "
            f"accounts_existing={self.accounts_existing}, "
            f"listings_created={self.listings_created}, images_created={self.images_created}, "
            f"accounts_removed={self.accounts_removed}, listings_removed={self.listings_removed}, "
            f"events_removed={self.events_removed})"
        )


def require_allowed() -> None:
    """Refuse to run under the production settings, or under any settings other than local and
    test unless `SEED_ALLOWED` is set. One plain line, no setting values."""
    # `settings.SETTINGS_MODULE` is None when a test runner configured the settings itself
    module = getattr(settings, "SETTINGS_MODULE", None) or os.environ.get(
        "DJANGO_SETTINGS_MODULE", ""
    )
    if module == PRODUCTION_MODULE:
        raise SeedRefused("Refused: sample data never runs under the production settings.")
    if module not in LOCAL_MODULES and getattr(settings, "SEED_ALLOWED", False) is not True:
        raise SeedRefused(
            "Refused: sample data runs under the local and test settings only (SEED_ALLOWED allows "
            "other development settings)."
        )
    if not _database_is_local() and getattr(settings, "SEED_ALLOWED", False) is not True:
        raise SeedRefused(
            "Refused: the database is not on this machine (SEED_ALLOWED allows a remote "
            "development database)."
        )


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _database_is_local() -> bool:
    host = str(settings.DATABASES.get("default", {}).get("HOST") or "").strip().lower()
    return host == "" or host in LOOPBACK_HOSTS or host.startswith("/")  # empty or a unix socket


def privileged_alias() -> str:
    """The connection that may delete publication events: the migrator alias where one is
    configured (local development), otherwise the default connection (tests, which run as the
    schema owner). The runtime role has no DELETE right on the events table."""
    return "migrator" if "migrator" in settings.DATABASES else "default"


def email_of(local: str) -> str:
    return f"{local}@{identity.SAMPLE_EMAIL_DOMAIN}"


def accounts_missing() -> int:
    """How many of the five sample accounts do not exist yet (0 means no password is needed)."""
    present = identity.sample_account_ids()
    return sum(1 for _, local, _, _ in ACCOUNTS if email_of(local).lower() not in present)


def _count(count) -> int:
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_LISTINGS:
        raise SeedRefused(f"--listings must be a whole number from 1 to {MAX_LISTINGS}.")
    return count


def _new_password() -> str:
    while True:
        candidate = secrets.token_urlsafe(14)
        if passwords.password_error(candidate) is None:
            return candidate


def _city():
    city = next((c for c in catalog.list_cities() if c.name == CITY_NAME), None)
    if city is None:
        raise SeedRefused("The Karachi reference data is missing; run the migrations first.")
    return city


def _ensure_accounts(report: Report, password: str | None, city, announce) -> list:
    """The five owner actors, creating any account that does not exist (each through the identity
    service, which writes its own audit record)."""
    present = identity.sample_account_ids()
    actors = []
    for kind, local, name, description in ACCOUNTS:
        email = email_of(local)
        account_id = present.get(email.lower())
        if account_id is None:
            if password is None:
                password = report.generated_password = _new_password()
            try:
                view = services.create_sample_account(
                    SYSTEM,
                    email,
                    password,
                    kind,
                    name,
                    city_id=city.id if kind == "agency" else None,
                    description=description,
                )
            except services.PasswordInvalid:
                raise SeedRefused("The password does not meet the password policy.") from None
            account_id = view.id
            report.accounts_created += 1
        else:
            report.accounts_existing += 1
        actors.append(Actor.user(account_id, "sample-data"))
    if report.generated_password and announce is not None:
        announce(report.generated_password)  # at once: a later failure must not lose it
    return actors


@cache
def _png(variant: int) -> bytes:
    """A small valid PNG (320 x 240) built with the standard library only (Pillow is imported by
    `kernel` alone). A colour gradient that depends on the variant."""
    width, height = 320, 240
    hue = (variant * 47) % 256
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter: none
        green = (hue + y) % 256
        rows.extend(
            itertools.chain.from_iterable(
                ((hue + x * 120 // width) % 256, green, (255 - hue) % 256) for x in range(width)
            )
        )

    def chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )


def row_for(index: int) -> dict:
    """The data of Listing `index` (0-based): the table row, varied for each later lap so that
    every title is unique and the prices differ, deterministically."""
    base = _ROWS[index % len(_ROWS)]
    lap = index // len(_ROWS)
    row = dict(base)
    if lap:
        jitter = random.Random(2026 + index).randint(-8, 8)  # noqa: S311 - not a secret
        amount, basis = base["price"]
        row["price"] = (str(max(1, int(amount) * (100 + jitter) // 100)), basis)
        row["title"] = f"{base['title']} (no. {lap + 1})"
    return row


def _values(row: dict, locality_ids: dict, city) -> dict:
    values = {
        "title": row["title"],
        "description": row["text"],
        "asking_price": row["price"][0],
        "price_basis": row["price"][1],
        "area_value": row["area"][0],
        "area_unit": row["area"][1],
        "city": str(city.id),
        "locality": str(locality_ids[row["locality"]]),
        **row["extra"],
    }
    if len(row["area"]) > 2:
        values["marla_basis"] = row["area"][2]
    if "period" in row:
        values["rental_period"] = row["period"]
    if "rooms" in row:
        values["bedrooms"], values["bathrooms"] = row["rooms"]
    if row.get("society"):
        values["society"] = row["society"]
    if row.get("block"):
        values["block"] = row["block"]
    if row.get("private"):
        values["street"], values["house_number"] = row["private"]
    if row["amenities"]:
        values["amenities"] = dict.fromkeys(row["amenities"], "present")
    return values


def _locality_ids(city) -> dict:
    """The ids of the localities the table names. A missing one is an error (the reference data
    differs from what the table expects), never a silent substitute."""
    found = {loc.name: loc.id for loc in catalog.list_localities(city.id) or ()}
    missing = sorted({row["locality"] for row in _ROWS} - set(found))
    if missing:
        raise SeedRefused(f"Karachi locality reference data is missing: {', '.join(missing)}.")
    return found


def _make_listing(index: int, owner: Actor, city, localities: dict, report: Report) -> None:
    row = row_for(index)
    draft = listings.create_draft(owner, *row["kind"])
    try:
        listings.apply_updates(owner, draft.id, draft.revision, _values(row, localities, city))
        for number in range(1 + index % 3):  # 1 to 3 images
            listings.upload_image(owner, draft.id, _png(index + number))
            report.images_created += 1
        current = selectors.get_owned_listing(owner, draft.id)
        publication.publish(
            SYSTEM,
            draft.id,
            current.revision,
            f"seed-{index:03d}",
            kind=publication.KIND_SEED,
        )
    except BaseException:
        with contextlib.suppress(DomainError):  # no half-made sample Listing stays behind
            listings.delete_draft(owner, draft.id)
        raise
    report.listings_created += 1


@contextlib.contextmanager
def _estimates_in_this_thread():
    """Publishing starts a price estimate in the background. A one-process tool has nobody
    waiting, so here each estimate runs in the calling thread and is stored when it returns."""
    before = settings.VALUATION_BACKGROUND_ESTIMATES
    settings.VALUATION_BACKGROUND_ESTIMATES = False
    try:
        yield
    finally:
        settings.VALUATION_BACKGROUND_ESTIMATES = before


def seed(
    listings_wanted: int = DEFAULT_LISTINGS, password: str | None = None, announce=None
) -> Report:
    """Create what is missing: accounts, then Listings 0..N-1 that do not exist yet (found by
    owner and title). Safe to repeat: a second run creates and changes nothing. A password is
    needed only when an account is created; none given means a random one (in the report, and
    passed to `announce` as soon as the accounts exist)."""
    require_allowed()
    wanted = _count(listings_wanted)
    report = Report()
    city = _city()
    localities = _locality_ids(city)
    owners = _ensure_accounts(report, password, city, announce)
    existing = [selectors.titles_owned_by(owner.account_id) for owner in owners]
    with _estimates_in_this_thread():
        for index in range(wanted):
            position = index % len(owners)
            found = existing[position].get(row_for(index)["title"])
            if found is not None and found[1] != "draft":
                continue  # already there
            if found is not None:  # a Draft left by an interrupted run: start that Listing again
                listings.delete_draft(owners[position], found[0])
            _make_listing(index, owners[position], city, localities, report)
    return report


def reset() -> Report:
    """Remove every record the command created and nothing else: the Listings of the sample
    accounts with their images, the `seed` publication events of those accounts (through the
    privileged connection), then the sample accounts. Audit records stay, as audit."""
    require_allowed()
    report = Report()
    accounts = identity.sample_account_ids()
    for listing_id in selectors.listing_ids_owned_by(accounts.values()):
        if listings.remove_sample_listing(SYSTEM, listing_id):
            report.listings_removed += 1
    report.events_removed = publication.remove_seed_events(
        SYSTEM, accounts.values(), using=privileged_alias()
    )
    for account_id in accounts.values():
        if services.remove_sample_account(SYSTEM, account_id):
            report.accounts_removed += 1
    return report


def preview_reset() -> Report:
    """What `reset` would remove, as counts (nothing is changed)."""
    require_allowed()
    accounts = identity.sample_account_ids()
    report = Report()
    report.accounts_removed = len(accounts)
    report.listings_removed = len(selectors.listing_ids_owned_by(accounts.values()))
    report.events_removed = publication.count_seed_events(accounts.values())
    return report


def reseed(
    listings_wanted: int = DEFAULT_LISTINGS, password: str | None = None, announce=None
) -> Report:
    """`reset` then `seed`; the report adds both."""
    _count(listings_wanted)
    require_allowed()
    removed = reset()
    made = seed(listings_wanted, password, announce)
    made.accounts_removed = removed.accounts_removed
    made.listings_removed = removed.listings_removed
    made.events_removed = removed.events_removed
    return made
