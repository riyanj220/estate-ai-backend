"""Gates 2 and 3 (Story 3.2): canary private values never leave through a response or a log.

Canaries are stored where private data lives (profile phones, the agency office address, account
emails, Listing private addresses and coordinates). Every route of the route table is then called
as a Visitor and as two accounts that own none of that data: `GET` (and `HEAD`) everywhere, other
methods only on routes that name an object, with an empty body, where they must be refused.
Responses and every log record of the run are searched. A route with a new path parameter needs
a value in `seed()`.
"""

import contextlib
import itertools
import logging
import re
import uuid
from urllib.parse import unquote

import pytest
from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.identity import selectors as identity
from apps.identity import services
from apps.identity.models import Account, AgencyProfile, IndividualProfile
from apps.kernel.actor import Actor
from apps.kernel.request_logging import REQUEST_LOGGER
from apps.listings import sample_data
from apps.listings.models import ListingImage, ListingPrivateAddress, PropertyListing, SavedListing
from tests.listings.support import (
    PASSWORD,
    make_account,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
    user,
)
from tests.privacy.audience import ADMIN, PUBLIC, audience, routes

pytestmark = pytest.mark.guard

# Invented values. A phone is searched for by its last ten digits, whatever its formatting.
PHONES = (
    "+923000001101",
    "+92 300 0001102",
    "0300-000-1103",
    "0300 0001104",
    "(0300)0001105",
)
SEPARATORS = re.compile(r"[\s\-().+_/]")


def squash(text: str) -> str:
    return re.sub(r"[\W_]", "", text.casefold())


class Canaries:
    def __init__(self):
        self.phones, self.words, self.exact = set(), set(), set()

    def phone(self, value):
        self.phones.add(re.sub(r"\D", "", value)[-10:])
        return value

    def address(self, value):  # also emails: matched ignoring case, spacing and punctuation
        self.words.add(squash(value))
        return value

    def coordinate(self, value):
        self.exact.add(value)
        return value

    def found_in(self, text: str) -> list[str]:
        text = unquote(text)
        digits, letters = SEPARATORS.sub("", text), squash(text)
        return sorted(
            [c for c in self.phones if c in digits]
            + [c for c in self.words if c in letters]
            + [c for c in self.exact if c in text]
        )


# saving or removing a favourite takes no body, so a viewer's own call may succeed
FAVOURITE = "/api/v1/me/favourites/<str:listing_id>"


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()


def seed(canaries):
    """Sample accounts and Listings (public, Draft, restricted, suspended owner) filled with
    canaries, and the values for every path parameter."""
    sample_data.seed(5)
    phones = itertools.cycle(PHONES)
    owners = list(identity.sample_account_ids().values())
    emails = []
    for number, account_id in enumerate(owners):
        email = canaries.address(f"Canary.Owner{number}@example.org")
        Account.objects.filter(id=account_id).update(email=email)
        emails.append(email)
    for profile in IndividualProfile.objects.filter(account_id__in=owners):
        IndividualProfile.objects.filter(id=profile.id).update(phone=canaries.phone(next(phones)))
    for number, profile in enumerate(AgencyProfile.objects.filter(account_id__in=owners)):
        AgencyProfile.objects.filter(id=profile.id).update(
            business_phone=canaries.phone(next(phones)),
            office_address=canaries.address(f"Suite {number}, Canaryoffice Tower"),
        )

    listings = list(PropertyListing.objects.filter(owner_account_id__in=owners).order_by("id"))
    assert len(listings) == 5
    for number, listing in enumerate(listings):
        ListingPrivateAddress.objects.update_or_create(
            listing_id=listing.id,
            defaults={
                "street": canaries.address(f"Canarystreet Walk {number}"),
                "house_number": canaries.address(f"HN-Canary-{number}"),
                "latitude": canaries.coordinate(f"24.86135{number}"),
                "longitude": canaries.coordinate(f"67.00975{number}"),
            },
        )

    by_type = {
        kind: list(Account.objects.filter(id__in=owners, account_type=kind).order_by("email"))
        for kind in ("individual", "agency")
    }
    png = sample_data._png(1)
    for kind, accounts in by_type.items():
        for account in accounts:
            services.set_profile_image(Actor.user(account.id, "r"), png, kind=kind)

    # one public Listing of each owner type stays; the others take the three non-public states
    public = [
        next(x for x in listings if x.owner_account_id in {a.id for a in by_type[kind]})
        for kind in ("individual", "agency")
    ]
    draft, restricted, suspended = [x for x in listings if x not in public]
    set_listing_visibility(draft.id, status="draft")
    set_listing_visibility(restricted.id, restricted=True)
    set_account_state(suspended.owner_account_id, "suspended")

    contexts = [
        {"listing_id": x.id, "image_id": ListingImage.objects.filter(listing_id=x.id)[0].id}
        for x in listings
    ]
    contexts += [  # the public profile id, never the account id
        {"agency_id": x.id} for x in AgencyProfile.objects.filter(account_id__in=owners)
    ]
    contexts += [{"account_id": account_id} for account_id in owners]
    contexts += [
        {"city_id": AgencyProfile.objects.filter(account_id__in=owners)[0].city_id},
        {"notification_id": uuid.UUID(int=1)},
        {"account_type": "individual"},
        {"session_id": uuid.UUID(int=1)},
    ]
    return {
        "contexts": contexts,
        "public": [str(x.id) for x in public],
        "public_agency": str(AgencyProfile.objects.get(account_id=public[1].owner_account_id).id),
        "owner_email": emails[0],
        "owner_listings": {str(x.id): x.owner_account_id for x in listings},
        "png": png,
    }


def urls_for(path, contexts) -> list[str]:
    names = re.findall(r"<(?:\w+:)?(\w+)>", path)
    if not names:
        return [path]
    found = {
        re.sub(r"<(?:\w+:)?(\w+)>", lambda m, c=context: str(c[m[1]]), path)
        for context in contexts
        if set(names) <= set(context)
    }
    assert found, f"cannot build a URL for {path}: add a value for {names} to seed()"
    return sorted(found)


def sign_in(email) -> Client:
    client = Client()
    response = client.post(
        "/api/v1/session",
        {"email": email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return client


def viewers(png):
    """A Visitor and one account of each type with their own (canary-free) profile and picture."""
    yield "visitor", Client()
    city_id = AgencyProfile.objects.first().city_id
    for kind in ("individual", "agency"):
        email = f"viewer.{kind}@example.com"
        account = make_account(email, kind)
        if kind == "individual":
            IndividualProfile.objects.create(
                account_id=account.id, full_name="Viewer One", phone="+920000000000"
            )
        else:
            AgencyProfile.objects.create(
                account_id=account.id,
                agency_name="Viewer Agency",
                business_phone="+920000000000",
                office_address="Viewer office",
                city_id=city_id,
            )
        services.set_profile_image(user(account), png, kind=kind)
        # favourites of public and non-public Listings, so the list has something to leak
        SavedListing.objects.bulk_create(
            SavedListing(account_id=account.id, listing_id=listing_id)
            for listing_id in PropertyListing.objects.values_list("id", flat=True)
        )
        yield kind, sign_in(email)


def response_text(response) -> str:
    body = b"".join(response.streaming_content) if response.streaming else response.content
    headers = [f"{name}: {value}" for name, value in response.items()]
    cookies = [cookie.OutputString() for cookie in response.cookies.values()]
    return "\n".join([body.decode("utf-8", "replace"), *headers, *cookies])


class Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def text(self, record) -> str:
        extra = {k: v for k, v in vars(record).items() if k not in ("msg", "args", "exc_info")}
        trace = logging.Formatter().formatException(record.exc_info) if record.exc_info else ""
        return f"{record.getMessage()}\n{extra}\n{trace}"


@contextlib.contextmanager
def all_logs():
    """Every record of the root logger and of each logger that does not propagate to it."""
    handler, root = Records(), logging.getLogger()
    apart = [
        logger
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger) and not logger.propagate
    ]
    level = root.level
    root.setLevel(logging.DEBUG)
    for logger in (root, *apart):
        logger.addHandler(handler)
    try:
        yield handler
    finally:
        root.setLevel(level)
        for logger in (root, *apart):
            logger.removeHandler(handler)


def test_no_canary_in_any_response_or_log():
    canaries = Canaries()
    data = seed(canaries)
    leaks, answered = [], set()
    with all_logs() as logs:
        for who, client in viewers(data["png"]):
            # `GET` first, so no other method can disturb what is read
            for unsafe in (False, True):
                for route in routes():
                    if unsafe and "<" not in route.path:
                        continue
                    methods = [m for m in route.methods if (m not in ("GET", "HEAD")) == unsafe]
                    for url in urls_for(route.path, data["contexts"]):
                        for method in methods:
                            response = client.generic(method, url, "{}", "application/json")
                            where = f"{who}: {method} {url} -> {response.status_code}"
                            assert response.status_code < 500, where
                            if unsafe and route.path != FAVOURITE:
                                assert response.status_code >= 400, f"not refused: {where}"
                            elif response.status_code == 200:
                                answered.add((route.path, url))
                            if audience(route) == ADMIN:
                                assert response.status_code in (401, 404), where
                            leaks += [
                                f"{where}: {hit}"
                                for hit in canaries.found_in(response_text(response))
                            ]
        records = list(logs.records)

    # not vacuous: each public GET answered, and so did every public Listing
    silent = [
        r.path
        for r in routes()
        if "GET" in r.methods and audience(r) == PUBLIC and r.path not in {p for p, _ in answered}
    ]
    assert not silent, f"no 200 from these public routes, so nothing was checked: {silent}"
    for listing_id in data["public"]:
        assert any(url.endswith(f"/public/listings/{listing_id}") for _, url in answered)
    for suffix in ("", "/listings"):  # an Agency page and its list (Story 3.8)
        assert any(
            url.endswith(f"/public/agencies/{data['public_agency']}{suffix}") for _, url in answered
        )
    assert not leaks, "private values in responses:\n" + "\n".join(leaks)

    assert any(r.name == REQUEST_LOGGER for r in records), "the request log was not captured"
    logged = [
        f"{r.name} {r.levelname}: {hits}"
        for r in records
        if (hits := canaries.found_in(logs.text(r)))
    ]
    assert not logged, "private values in logs:\n" + "\n".join(logged)


def test_the_owner_sees_the_canaries_the_detector_looks_for():
    """Positive control: the canaries are really stored where the application reads them."""
    canaries = Canaries()
    data = seed(canaries)
    listing_id, owner_id = next(iter(data["owner_listings"].items()))
    set_account_state(owner_id, "active")
    account = Account.objects.get(id=owner_id)
    Account.objects.filter(id=owner_id).update(password=make_password(PASSWORD))
    client = sign_in(account.email)
    own = response_text(client.get(f"/api/v1/listings/{listing_id}"))
    assert len(canaries.found_in(own)) >= 4  # street, house number, both coordinates
    profile = "agency-profile" if account.account_type == "agency" else "profile"
    assert canaries.found_in(response_text(client.get(f"/api/v1/me/{profile}")))


@pytest.mark.parametrize(
    "text",
    [
        '{"x": "+92 300 000-1101"}',
        '{"x": "(0300) 0001101"}',
        "X-Owner: Canary.One%40example.org",
        "CANARY STREET, walk 1",
        "lat=24.861351",
    ],
)
def test_the_detector_sees_other_formats(text):
    canaries = Canaries()
    canaries.phone("+923000001101")
    canaries.address("canary.one@example.org")
    canaries.address("Canary Street Walk 1")
    canaries.coordinate("24.861351")
    assert canaries.found_in(text)
    assert not canaries.found_in('{"id": "7f0e", "price": "12500000"}')
