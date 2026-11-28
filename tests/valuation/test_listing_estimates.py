"""Stories 4.5 to 4.7: estimate on publish and edit, the public estimate, comparison, forecast."""

# ruff: noqa: F811 - the fixtures of test_estimates are parameters here

import uuid
from decimal import Decimal

import pytest
from django.test import Client

from apps.kernel import runtime
from apps.kernel.errors import DomainError
from apps.listings import publication
from apps.listings import selectors as listings_read
from apps.listings import services as listings
from apps.valuation import comparison, forecasting, selectors, services
from apps.valuation.data import localities
from apps.valuation.models import PriceEstimate
from tests.listings.support import (
    SYSTEM,
    ready_draft,
    set_account_state,
    set_listing_visibility,
    user,
)
from tests.valuation.test_estimates import (  # noqa: F401 - fixtures
    client,
    clifton,
    draft_url,
    email,
    other_client,
    owner,
    post,
    setup,
)

pytestmark = pytest.mark.django_db


def draft(owner, values=("sale", "house", "house"), **extra):
    place = clifton()
    data = {"city": str(place.city_id), "locality": str(place.id), **extra}
    return ready_draft(owner, values, data)  # with an image, so that it can be published


def url(listing_id):
    return f"/api/v1/public/listings/{listing_id}/estimate"


@pytest.fixture
def commit(django_capture_on_commit_callbacks):
    return lambda: django_capture_on_commit_callbacks(execute=True)


def publish(owner, view, commit):
    with commit():
        return publication.publish(user(owner), view.id, view.revision, uuid.uuid4().hex)


def edit(owner, listing_id, updates, commit):
    revision = listings_read.get_owned_listing(user(owner), listing_id).revision
    with commit():
        return listings.apply_updates(user(owner), listing_id, revision, updates)


def visitor_read(listing_id):
    response = Client().get(url(listing_id))
    assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "no-store"
    return response.json()


def test_publishing_stores_an_estimate_that_a_visitor_reads(owner, commit):
    view = draft(owner, bedrooms=3, amenities={})
    publish(owner, view, commit)
    row = PriceEstimate.objects.get()
    assert (row.listing_id, row.account_id) == (view.id, owner.id)
    data = visitor_read(view.id)
    assert (data["status"], data["estimated_value"]) == ("available", f"{row.value:.2f}")
    assert data["asking_total"] == "1500000.00" and data["can_recompute"] is False
    assert data["comparison"]["verdict"] == "below"
    forecast = data["forecast"]
    assert forecast["horizon_years"] == 5
    assert row.value < Decimal(forecast["low"]) <= Decimal(forecast["base"])
    assert Decimal(forecast["base"]) <= Decimal(forecast["high"])
    assert row.forecast == forecasting.compute(row.value, row.inputs["locality"])
    assert PriceEstimate.objects.count() == 1


def test_an_edit_recomputes_and_a_failed_one_is_unavailable_not_stale(owner, commit, monkeypatch):
    view = draft(owner)
    publish(owner, view, commit)
    first = visitor_read(view.id)["estimated_value"]
    edit(owner, view.id, {"bedrooms": 6}, commit)
    assert PriceEstimate.objects.count() == 2
    edit(owner, view.id, {"asking_price": "2000000"}, commit)  # not a model input
    assert PriceEstimate.objects.count() == 2

    def broken(*args):
        raise RuntimeError("x")

    monkeypatch.setattr(services, "_infer", broken)
    edit(owner, view.id, {"bedrooms": 7}, commit)
    data = visitor_read(view.id)
    assert (data["status"], data["reason"]) == ("unavailable", "temporarily_unavailable")
    assert data["estimated_value"] is None and data["forecast"] is None
    assert data["comparison"] is None and first is not None


def test_only_a_signed_in_reader_repairs_a_missing_estimate(owner, client):
    view = draft(owner)
    set_listing_visibility(view.id)  # published without the event
    assert visitor_read(view.id)["status"] == "unavailable"
    assert not PriceEstimate.objects.exists()
    other = other_client().get(url(view.id)).json()
    assert other["status"] == "available" and other["can_recompute"] is False
    assert PriceEstimate.objects.get().account_id == owner.id
    mine = client.get(url(view.id)).json()
    assert mine["can_recompute"] is True and PriceEstimate.objects.count() == 1


def test_the_owner_recomputes_a_published_listing(owner, client):
    view = draft(owner)
    set_listing_visibility(view.id)
    assert post(client, draft_url(view.id)).json()["status"] == "available"
    assert post(client, draft_url(view.id)).status_code == 200
    assert PriceEstimate.objects.count() == 2
    assert post(other_client(), draft_url(view.id)).status_code == 404


def test_hidden_listings_are_not_found(owner, client):
    view = draft(owner)
    readers = (Client(), other_client(), client)
    for reader in readers:  # a Draft
        assert reader.get(url(view.id)).status_code == 404
    set_listing_visibility(view.id, restricted=True)
    assert Client().get(url(view.id)).status_code == 404
    set_listing_visibility(view.id)
    set_account_state(owner.id, "suspended")
    assert Client().get(url(view.id)).status_code == 404
    assert Client().get(url("nope")).status_code == 404
    assert not PriceEstimate.objects.exists()


def test_an_unsupported_listing_has_a_reason_and_no_forecast(owner, commit):
    view = draft(owner, ("rent", "house", "house"))
    publish(owner, view, commit)
    data = visitor_read(view.id)
    assert (data["reason"], data["forecast"], data["asking_total"]) == (
        "rent_not_supported",
        None,
        None,
    )
    assert not PriceEstimate.objects.exists()


@pytest.mark.parametrize("fails", ["handler", "inference"])
def test_publication_is_unchanged_when_the_handler_fails(owner, commit, monkeypatch, fails):
    def broken(*args):
        raise RuntimeError("x")

    name = "schedule_listing_estimate" if fails == "handler" else "_infer"
    monkeypatch.setattr(services, name, broken)
    view = draft(owner)
    assert publish(owner, view, commit).status == "published"
    assert not PriceEstimate.objects.exists()


def test_publication_only_hands_the_work_over(owner, commit, settings, monkeypatch):
    settings.VALUATION_BACKGROUND_ESTIMATES = True
    handed = []
    monkeypatch.setattr(runtime, "submit_background", lambda *args: handed.append(args))
    monkeypatch.setattr(runtime, "submit_heavy", lambda *args: pytest.fail("heavy slot used"))
    view = draft(owner)
    publish(owner, view, commit)
    assert handed[0][2] == view.id and not PriceEstimate.objects.exists()

    def busy(*args):
        raise runtime.ExecutorBusy()

    monkeypatch.setattr(runtime, "submit_background", busy)
    services.schedule_listing_estimate(SYSTEM, view.id)  # dropped quietly
    task, *args = handed[0]
    task(*args)
    assert PriceEstimate.objects.get().listing_id == view.id


def test_a_draft_edit_computes_nothing(owner, commit):
    view = draft(owner)
    edit(owner, view.id, {"bedrooms": 5}, commit)
    assert not PriceEstimate.objects.exists()
    assert selectors.latest_draft_estimate(user(owner), view.id) is None


@pytest.mark.parametrize(
    "asking, verdict, percent",
    [
        ("100", "about_equal", "0.0"),
        ("105", "about_equal", "5.0"),
        ("95", "about_equal", "5.0"),
        ("105.01", "above", "5.0"),
        ("120", "above", "20.0"),
        ("80", "below", "20.0"),
    ],
)
def test_comparison_boundaries(asking, verdict, percent):
    found = comparison.compare(Decimal(asking), Decimal(100))
    assert (found.verdict, str(found.percent)) == (verdict, percent)


def test_total_asking_price():
    total = comparison.total_asking_price
    assert total("1500000.00", "total", "10") == Decimal("1500000.00")
    assert total("150000", "per_unit", "10.5000") == Decimal("1575000.00")
    assert total("50000", "rental_period", "10") is None
    assert total(None, "total", "10") is None


def test_the_forecast_is_ordered_and_reproducible_for_every_locality():
    for name in localities.load().base_rates:
        found = forecasting.compute(Decimal("10000000"), name)
        assert found == forecasting.compute(Decimal("10000000"), name)
        assert Decimal(found["low"]) <= Decimal(found["base"]) <= Decimal(found["high"])
    premium = next(n for n, tier in localities.load().tiers.items() if tier == "premium")
    assert forecasting.compute(Decimal("10000000"), premium)["base"] == "15386000.00"


def test_an_estimate_says_what_moves_it(owner, commit):
    view = draft(owner, bedrooms=3, bathrooms=3, condition="new", construction_year=2025)
    publish(owner, view, commit)
    data = visitor_read(view.id)
    found = {item["factor"]: Decimal(item["percent"]) for item in data["drivers"]}
    assert found["condition"] > 0 and "locality" in found
    assert set(found) <= {"locality", "bedrooms", "bathrooms", "building_age", "condition"}
    sizes = [abs(Decimal(item["percent"])) for item in data["drivers"]]
    assert sizes == sorted(sizes, reverse=True) and min(sizes) >= 1


def test_a_failed_explanation_keeps_the_estimate(owner, commit, monkeypatch):
    def broken(*args):
        raise RuntimeError("x")

    monkeypatch.setattr(services.drivers, "compute", broken)
    publish(owner, draft(owner), commit)
    row = PriceEstimate.objects.get()
    assert row.value > 0 and row.drivers is None


def test_stored_estimates_without_drivers_are_filled_once_and_keep_their_value(owner, commit):
    view = draft(owner, condition="new")
    publish(owner, view, commit)
    row = PriceEstimate.objects.get()
    PriceEstimate.objects.update(drivers=None)
    assert visitor_read(view.id)["drivers"] == []
    assert services.explain_stored_estimates(SYSTEM) == 1
    assert services.explain_stored_estimates(SYSTEM) == 0
    again = PriceEstimate.objects.get()
    assert (again.value, again.drivers) == (row.value, row.drivers)
    with pytest.raises(DomainError):
        services.explain_stored_estimates(user(owner))
