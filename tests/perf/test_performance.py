"""NFR-1 budgets on the large fixture (Story 3.4). Not part of the default run:

    uv run pytest -m perf -s

Server time is the whole request through the Django test client (routing, middleware, view,
queries, serialisation), without network or browser. Run it serially on an idle machine.
"""

import math
import time

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from tests.perf import fixture

SEARCH_BUDGET_MS = 500
DETAIL_BUDGET_MS = 300
SEARCH_RUNS = 20  # per query shape
DETAIL_RUNS = 200
URL = "/api/v1/public/listings"

pytestmark = pytest.mark.perf


def get(url):
    response = Client().get(url)
    assert response.status_code == 200, (url, response.content)
    return response.json()


def timed(url):
    started = time.perf_counter()
    get(url)
    return (time.perf_counter() - started) * 1000


def percentile(samples, share):
    return sorted(samples)[math.ceil(share * len(samples)) - 1]


def report(label, samples):
    p50, p95 = percentile(samples, 0.5), percentile(samples, 0.95)
    print(f"{label:<28} p50 {p50:6.1f} ms   p95 {p95:6.1f} ms   max {max(samples):6.1f} ms")
    return p95


def search_urls(made):
    """The conventional search shapes: name -> URL."""
    city, locality = made.city_ids[0], made.locality_ids[0]
    amenities = ",".join(made.amenity_codes[:2])
    queries = {
        "newest, no filter": "",
        "newest, 50 per page": "limit=50",
        "city and purpose": f"purpose=sale&city={city}",
        "locality and category": f"locality={locality}&category=house",
        "price range, bedrooms": "purpose=sale&asking_price_min=5000000"
        "&asking_price_max=50000000&bedrooms_min=3",
        "area range": "area_sqft_min=1000&area_sqft_max=3000",
        "amenities": f"amenities={amenities}",
        "price ascending": f"purpose=sale&city={city}&sort=price_asc",
        "price descending": "sort=price_desc",
        "relevance": f"city={city}&prefer_bedrooms_min=3&prefer_amenities={amenities}",
        "everything at once": f"purpose=rent&city={city}&category=apartment&bedrooms_min=2"
        f"&asking_price_max=300000&amenities={made.amenity_codes[0]}",
        "no match": "asking_price_max=1",
    }
    urls = {name: f"{URL}?{query}" for name, query in queries.items()}
    cursor_url = f"{URL}?limit=50"
    for _ in range(20):  # a deep page: 1,000 Listings down
        cursor_url = get(cursor_url)["next"]
    urls["newest, page 21"] = cursor_url
    return urls


def test_search_and_detail_meet_the_budgets(db):
    started = time.perf_counter()
    made = fixture.generate()
    print(
        f"\nfixture: {len(made.published_ids)} published, {len(made.hidden_ids)} hidden, "
        f"{len(made.owner_ids)} owners in {time.perf_counter() - started:.1f} s"
    )

    urls = search_urls(made)
    for name, url in urls.items():
        assert bool(get(url)["results"]) is (name != "no match"), name  # also the warm-up

    everything, slow = [], {}
    for name, url in urls.items():
        samples = [timed(url) for _ in range(SEARCH_RUNS)]
        everything += samples
        if (p95 := report(name, samples)) >= SEARCH_BUDGET_MS:
            slow[name] = p95
    report("SEARCH (all)", everything)

    step = len(made.published_ids) // DETAIL_RUNS
    detail = [timed(f"{URL}/{key}") for key in made.published_ids[::step][:DETAIL_RUNS]]
    detail_p95 = report("DETAIL", detail)

    assert not slow, f"search p95 over {SEARCH_BUDGET_MS} ms: {slow}"
    assert detail_p95 < DETAIL_BUDGET_MS

    # the newest-first list is read from its partial index, first page and deep page alike
    for name in ("newest, no filter", "newest, page 21"):
        with CaptureQueriesContext(connection) as captured:
            get(urls[name])
        with connection.cursor() as cursor:
            cursor.execute(f"EXPLAIN (ANALYZE) {captured[0]['sql']}")
            plan = "\n".join(row[0] for row in cursor.fetchall())
        print(f"\nEXPLAIN ANALYZE {name}\n{plan}")
        assert "listings_public_newest_idx" in plan, name
