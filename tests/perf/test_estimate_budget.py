"""Story 4.3: estimate p95 under 1 second (`uv run pytest -m perf -s`)."""

import time

import pytest

from apps.catalog.models import City, Locality
from apps.valuation import services
from tests.listings.support import SYSTEM, make_account, user

pytestmark = [pytest.mark.perf, pytest.mark.django_db]


def test_estimate_p95_is_under_one_second():
    services.register_model(SYSTEM)
    karachi, _ = City.objects.get_or_create(name="Karachi")
    area, _ = Locality.objects.get_or_create(
        city=karachi, name="Clifton", defaults={"kind": "area"}
    )
    actor = user(make_account())
    values = {"locality": str(area.id), "area_value": "240", "area_unit": "sqyd"}
    timings = []
    for _ in range(100):
        started = time.perf_counter()
        outcome = services.estimate_for_attributes(actor, "sale", "house", "house", values)
        timings.append(time.perf_counter() - started)
        assert outcome.reason is None
    p95 = sorted(timings)[94]
    print(f"estimate p95 {p95 * 1000:.1f} ms, max {max(timings) * 1000:.1f} ms")
    assert p95 < 1
