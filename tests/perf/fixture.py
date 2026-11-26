"""The large search fixture (Story 3.4): about 10,000 published Listings without images.

`generate()` bulk-inserts rows straight into the tables, so it takes seconds, not hours. It is test
code on purpose: the application may only set a Listing to published and write a
`PublicationEvent` in `listings.publication` (architecture guards over `apps/`), and nothing in
the running product should be able to do otherwise. It is NOT the sample marketplace data
(`apps/listings/sample_data.py`): its accounts are on their own reserved domain, they cannot sign
in, every title starts with `TITLE_PREFIX`, and there are no images.

The same arguments always make the same rows (ids included); only the reference-data ids (cities,
localities) come from the database. The rows are valid under the database constraints but have not
been through the publication checks, so use them for read and performance tests only.
"""

import random
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.db import connection

from apps.catalog import registry
from apps.catalog import selectors as catalog
from apps.identity.models import Account
from apps.listings import area
from apps.listings.models import (
    ListingAmenity,
    ListingArea,
    ListingCommercial,
    ListingLocation,
    ListingPlot,
    ListingResidential,
    ListingTerms,
    PropertyListing,
    PublicationEvent,
)

DEFAULT_COUNT = 10_000
EMAIL_DOMAIN = "perf.estate-ai.invalid"
TITLE_PREFIX = "[perf]"
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)  # creation times are spread over the following 300 days
BATCH = 2000
# (category, subtype, weight)
KINDS = (
    ("house", "house", 35),
    ("apartment", "apartment", 30),
    ("plot", "residential_plot", 15),
    ("plot", "commercial_plot", 5),
    ("commercial", "shop", 8),
    ("commercial", "office", 7),
)
PLOT_AREAS = (("5", "marla"), ("10", "marla"), ("1", "kanal"), ("120", "sqyd"), ("500", "sqyd"))


@dataclass(frozen=True)
class Fixture:
    published_ids: tuple[uuid.UUID, ...]
    hidden_ids: tuple[uuid.UUID, ...]  # drafts and archived Listings: never public
    owner_ids: tuple[uuid.UUID, ...]
    city_ids: tuple[uuid.UUID, ...]  # busiest first
    locality_ids: tuple[uuid.UUID, ...]  # localities of the busiest city
    amenity_codes: tuple[str, ...]  # most common first


def _choice(rng, field_id):
    return rng.choice(registry.field(field_id).allowed_values)


def generate(count: int = DEFAULT_COUNT, *, seed: int = 34) -> Fixture:
    """`count` published Listings, plus a tenth as many hidden ones (drafts and archived), owned
    by `count // 40` Individual and `count // 200` Agency accounts (agencies hold half of the
    Listings). A fifth of the published ones have a second publication event."""
    rng = random.Random(seed)  # noqa: S311 - test data, not a secret

    def new_id():
        return uuid.UUID(int=rng.getrandbits(128), version=4)

    individuals = max(2, count // 40)
    agencies = max(1, count // 200)
    accounts = [
        Account(
            id=new_id(),
            email=f"perf.{kind}.{n}@{EMAIL_DOMAIN}",
            password=UNUSABLE_PASSWORD_PREFIX,
            account_type=kind,
            email_verified_at=EPOCH,
        )
        for kind, total in (("individual", individuals), ("agency", agencies))
        for n in range(total)
    ]
    Account.objects.bulk_create(accounts, batch_size=BATCH)

    cities = catalog.list_cities()
    localities = {c.id: [loc.id for loc in catalog.list_localities(c.id) or ()] for c in cities}
    # the cities with most localities take most Listings, so that a city filter is realistic
    cities = sorted(cities, key=lambda c: (-len(localities[c.id]), c.name))
    city_weights = [1 / (rank + 1) for rank in range(len(cities))]
    codes = [a.code for a in catalog.list_amenities()]
    code_weights = [1 / (rank + 3) for rank in range(len(codes))]

    tables = (
        PropertyListing,
        ListingTerms,
        ListingArea,
        ListingResidential,
        ListingPlot,
        ListingCommercial,
        ListingLocation,
        ListingAmenity,
        PublicationEvent,
    )
    rows: dict = {model: [] for model in tables}
    published, hidden, used_codes = [], [], Counter()

    for n in range(count + count // 10):
        public = n < count
        key = new_id()
        (published if public else hidden).append(key)
        owner = (
            accounts[individuals + rng.randrange(agencies)]
            if rng.random() < 0.5
            else accounts[rng.randrange(individuals)]
        )
        category, subtype, _ = rng.choices(KINDS, weights=[w for *_, w in KINDS])[0]
        purpose = "sale" if category == "plot" or rng.random() < 0.65 else "rent"
        created = EPOCH + timedelta(seconds=rng.randrange(300 * 24 * 3600))
        rows[PropertyListing].append(
            PropertyListing(
                id=key,
                owner_account_id=owner.id,
                status="published" if public else rng.choice(("draft", "archived")),
                registry_version=registry.registry_version(),
                purpose=purpose,
                category=category,
                subtype=subtype,
                title=f"{TITLE_PREFIX} {subtype.replace('_', ' ')} no. {n}",
                description="A generated Listing for search and performance tests. " * 4,
                created_at=created,
                updated_at=created,
            )
        )

        if purpose == "sale":
            price = rng.randrange(20, 2000) * 100_000
            terms = {"asking_price": Decimal(price), "price_basis": "total"}
        else:
            price = rng.randrange(20, 800) * 1000
            terms = {
                "asking_price": Decimal(price),
                "price_basis": "rental_period",
                "rental_period": "month",
            }
        terms["price_negotiable"] = rng.choice((True, False, None))
        rows[ListingTerms].append(ListingTerms(listing_id=key, **terms))

        if category == "plot":
            value, unit = rng.choice(PLOT_AREAS)
            value = Decimal(value)
        else:
            value, unit = Decimal(rng.randrange(300, 6000)), "sqft"
        basis = Decimal("225") if unit in area.MARLA_UNITS else None
        rows[ListingArea].append(
            ListingArea(
                listing_id=key,
                area_value=value,
                area_unit=unit,
                marla_basis=basis,
                area_sqft=area.normalised_sqft(value, unit, basis),
            )
        )

        if category in ("house", "apartment"):
            rows[ListingResidential].append(
                ListingResidential(
                    listing_id=key,
                    bedrooms=rng.randrange(1, 8),
                    bathrooms=rng.randrange(1, 7),
                    parking_spaces=rng.choice((None, 0, 1, 2, 3)),
                    furnishing=_choice(rng, "furnishing"),
                    condition=_choice(rng, "condition"),
                    construction_year=rng.randrange(1980, 2026),
                )
            )
        elif category == "plot":
            rows[ListingPlot].append(
                ListingPlot(
                    listing_id=key,
                    corner_plot=rng.choice((True, False, None)),
                    possession_status=_choice(rng, "possession_status"),
                )
            )
        else:
            rows[ListingCommercial].append(
                ListingCommercial(listing_id=key, business_use=_choice(rng, "business_use"))
            )

        city = rng.choices(cities, weights=city_weights)[0]
        rows[ListingLocation].append(
            ListingLocation(
                listing_id=key,
                city=city.id,
                locality=rng.choice(localities[city.id]) if localities[city.id] else None,
            )
        )

        answered = set(rng.choices(codes, weights=code_weights, k=rng.randrange(0, 7)))
        for code in sorted(answered):
            state = "present" if rng.random() < 0.85 else "none"
            used_codes[code] += state == "present"
            rows[ListingAmenity].append(
                ListingAmenity(listing_id=key, amenity_code=code, state=state)
            )

        row = rows[PropertyListing][-1]
        if row.status != "draft":
            first = row.first_published_at = created + timedelta(hours=rng.randrange(1, 72))
            times = [first]
            if rng.random() < 0.2:  # republished once
                times.append(first + timedelta(days=rng.randrange(1, 30)))
            for number, at in enumerate(times):
                rows[PublicationEvent].append(
                    PublicationEvent(
                        id=new_id(),
                        account_id=owner.id,
                        listing_id=key,
                        kind="republish_free" if number else "seed",
                        counted=False,
                        created_at=at,
                    )
                )

    for model in tables:
        model.objects.bulk_create(rows[model], batch_size=BATCH)
    with connection.cursor() as cursor:  # the planner knows nothing of rows just inserted
        for model in (Account, *tables):
            cursor.execute(f"ANALYZE {connection.ops.quote_name(model._meta.db_table)}")

    return Fixture(
        published_ids=tuple(published),
        hidden_ids=tuple(hidden),
        owner_ids=tuple(a.id for a in accounts),
        city_ids=tuple(c.id for c in cities),
        locality_ids=tuple(localities[cities[0].id]),
        amenity_codes=tuple(code for code, _ in used_codes.most_common()),
    )
