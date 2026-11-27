"""Story 4.3: requesting an AI Price Estimate, supportability, storage and removal."""

import itertools
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.catalog import registry
from apps.catalog.models import City, Locality
from apps.kernel import runtime
from apps.kernel.models import AiRequestLog, RateLimitPolicy
from apps.listings import services as listings
from apps.valuation import features, retention, selectors, services, supportability
from apps.valuation.models import PriceEstimate
from tests.listings.support import (
    PASSWORD,
    SYSTEM,
    make_account,
    random_email,
    ready_draft,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
    user,
)

pytestmark = pytest.mark.django_db

URL = "/api/v1/estimates"


def draft_url(listing_id):
    return f"/api/v1/listings/{listing_id}/estimate"


def clifton():
    karachi, _ = City.objects.get_or_create(name="Karachi")
    area, _ = Locality.objects.get_or_create(
        city=karachi, name="Clifton", defaults={"kind": "area"}
    )
    return area


@pytest.fixture(autouse=True)
def setup(db):
    seed_cap()
    seed_media_policies()
    RateLimitPolicy.objects.update_or_create(
        scope="estimates",
        defaults={"limit": 20, "window_seconds": 3600, "key_kind": "account", "note": "t"},
    )
    services.register_model(SYSTEM)


def sign_in(account_email):
    client = Client(enforce_csrf_checks=True)
    token = client.get("/api/v1/csrf").json()["csrf_token"]
    response = client.post(
        "/api/v1/session",
        {"email": account_email, "password": PASSWORD},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content
    client.token = client.get("/api/v1/csrf").json()["csrf_token"]
    return client


@pytest.fixture
def email():
    return random_email()


@pytest.fixture
def owner(email):
    return make_account(email)


@pytest.fixture
def client(owner, email):
    return sign_in(email)


def other_client():
    email = random_email()
    make_account(email)
    return sign_in(email)


def post(client, url, body=None):
    return client.post(
        url,
        body or {},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=getattr(client, "token", ""),
    )


def body(**fields):
    values = {"locality": str(clifton().id), "area_value": "240", "area_unit": "sqyd"}
    values |= fields
    return {"purpose": "sale", "category": "house", "subtype": "house", "fields": values}


def draft(owner, values=("sale", "house", "house"), **extra):
    place = clifton()
    data = {"city": str(place.city_id), "locality": str(place.id), **extra}
    return ready_draft(owner, values, data, images=0)


def test_an_estimate_for_entered_attributes_is_stored_and_reproducible(client, owner):
    response = post(client, URL, body(bedrooms=4, amenities={}))
    assert response.status_code == 200, response.content
    data = response.json()
    assert (data["status"], data["currency"], data["model_version"]) == ("available", "PKR", 1)
    assert data["reason"] is None and data["message"] is None
    assert Decimal(data["estimated_value"]) > 0 and response["Cache-Control"] == "no-store"
    row = PriceEstimate.objects.get()
    assert row.account_id == owner.id and row.listing_id is None
    assert row.feature_schema_version == features.FEATURE_SCHEMA_VERSION
    assert row.inputs_sha256 == features.snapshot_hash(row.inputs)
    assert tuple(sorted(row.inputs)) == tuple(sorted(features.SOURCE_FIELDS))
    model = selectors.load_model(selectors.current_model())
    again = model.estimate(features.build_features(row.inputs))
    assert abs(Decimal(again) - row.value) <= services.ROUND_TO
    log = AiRequestLog.objects.get()
    assert (log.feature, log.provider, log.outcome) == ("price_estimate", "local", "success")


def test_a_draft_estimate_uses_only_model_inputs(client, owner):
    view = draft(
        owner,
        street="Canarystreet Walk 7",
        house_number="HN-CANARY-7",
        latitude="24.861357",
        longitude="67.009757",
        asking_price="987654321",
    )
    first = post(client, draft_url(view.id)).json()
    assert first["status"] == "available"
    row = PriceEstimate.objects.get()
    assert row.listing_id == view.id
    stored = repr(row.inputs)
    for canary in ("Canarystreet", "HN-CANARY", "24.861357", "67.009757", "987654321", "+92"):
        assert canary not in stored
    assert set(row.inputs) == set(features.SOURCE_FIELDS)
    assert all(registry.field(name).valuation_input for name in row.inputs)
    # the Asking price is untouched
    assert (
        client.get(f"/api/v1/listings/{view.id}")
        .json()["fields"]["asking_price"]
        .startswith("987654321")
    )


def test_visitors_get_401_and_nothing_is_computed(owner):
    view = draft(owner)
    visitor = Client()
    for url in (URL, draft_url(view.id)):
        assert post(visitor, url, body()).status_code == 401
    assert not PriceEstimate.objects.exists() and not AiRequestLog.objects.exists()


def test_other_accounts_unknown_ids_non_drafts_and_suspended(client, owner):
    view = draft(owner)
    other = other_client()
    for url in (draft_url(view.id), draft_url(uuid.uuid4()), draft_url("nope")):
        assert post(other, url).status_code == 404
    set_listing_visibility(view.id, status="sold")
    assert post(client, draft_url(view.id)).status_code == 409
    set_account_state(owner.id, "suspended")
    assert post(client, URL, body()).status_code == 403
    assert not PriceEstimate.objects.exists()


@pytest.mark.parametrize(
    "change, field",
    [
        ({"fields": {"asking_price": "1"}}, "fields"),
        ({"fields": {"street": "x"}}, "fields"),
        ({"fields": {"area_value": "-1", "area_unit": "sqyd"}}, "area_value"),
        ({"fields": {"locality": str(uuid.UUID(int=1))}}, "locality"),
        ({"subtype": "shop"}, "subtype"),
        ({"purpose": "lease"}, "purpose"),
        ({"fields": []}, "fields"),
    ],
)
def test_invalid_attributes_are_refused(client, change, field):
    response = post(client, URL, {**body(), **change})
    assert response.status_code == 400, response.content
    assert field in response.json()["error"]["fields"]


def test_the_21st_request_in_an_hour_is_throttled(client):
    rent = {**body(), "purpose": "rent"}  # unsupported requests count too
    for _ in range(20):
        assert post(client, URL, rent).status_code == 200
    response = post(client, URL, rent)
    assert response.status_code == 429 and response["Retry-After"]
    assert response.json()["error"]["code"] == "throttled"


def test_every_taxonomy_combination_is_classified_as_specified():
    supported = {
        ("sale", "house", "house"),
        ("sale", "apartment", "apartment"),
        ("sale", "plot", "residential_plot"),
    }
    pairs = [(c.id, s) for c in registry.categories() for s in c.subtypes]
    cases = itertools.product(
        registry.purposes(),
        pairs,
        [("Karachi", "Clifton"), ("Karachi", "Nowhere"), ("Lahore", "Clifton"), (None, None)],
        [("10", "marla", "225"), ("10", "marla", None), ("1", "kanal", None),
         ("240", "sqyd", None), (None, None, None)],
    )  # fmt: skip
    for purpose, (category, subtype), (city, locality), (value, unit, basis) in cases:
        got = supportability.classify(
            purpose=purpose,
            category=category,
            subtype=subtype,
            city=city,
            locality=locality,
            area_value=value,
            area_unit=unit,
            marla_basis=basis,
            covered={"Clifton"},
        )
        if purpose != "sale":
            expected = "rent_not_supported"
        elif (purpose, category, subtype) not in supported:
            expected = "type_not_supported"
        elif locality is None:
            expected = "locality_missing"
        elif (city, locality) != ("Karachi", "Clifton"):
            expected = "locality_not_covered"
        elif unit is None:
            expected = "area_missing"
        elif unit in ("marla", "kanal") and basis is None:
            expected = "marla_basis_unknown"
        else:
            expected = None
        assert got == expected
        assert got is None or got in supportability.MESSAGES


def test_unsupported_has_a_reason_and_no_number(client, owner):
    view = draft(owner, ("rent", "house", "house"))
    data = post(client, draft_url(view.id)).json()
    assert data == {
        "status": "unavailable",
        "estimated_value": None,
        "currency": "PKR",
        "model_version": None,
        "estimated_at": None,
        "reason": "rent_not_supported",
        "message": supportability.MESSAGES["rent_not_supported"],
    }
    data = post(client, URL, body(area_unit="marla")).json()
    assert data["reason"] == "marla_basis_unknown"
    assert not PriceEstimate.objects.exists() and not AiRequestLog.objects.exists()


def test_a_failed_computation_is_unavailable_and_logged_without_content(client, monkeypatch):
    def broken(*args):
        raise RuntimeError("Canary failure text")

    monkeypatch.setattr(services, "_infer", broken)
    data = post(client, URL, body()).json()
    assert (data["status"], data["reason"]) == ("unavailable", "temporarily_unavailable")
    assert data["estimated_value"] is None
    assert AiRequestLog.objects.get().outcome == "error"

    def busy(*args):
        raise runtime.ExecutorBusy()

    monkeypatch.setattr(runtime, "submit_heavy", busy)
    assert post(client, URL, body()).json()["reason"] == "temporarily_unavailable"
    assert AiRequestLog.objects.filter(outcome="fallback").count() == 1
    assert not PriceEstimate.objects.exists()


def test_deleting_a_draft_removes_its_estimates(client, owner, django_capture_on_commit_callbacks):
    view, kept = draft(owner), draft(owner)
    for listing_id in (view.id, kept.id):
        post(client, draft_url(listing_id))
    post(client, URL, body())
    with django_capture_on_commit_callbacks(execute=True):
        listings.delete_draft(user(owner), view.id)
    assert sorted(PriceEstimate.objects.values_list("listing_id", flat=True), key=str) == sorted(
        [kept.id, None], key=str
    )


def test_the_retention_job_removes_orphans_and_old_ad_hoc_estimates(client, owner):
    view, kept = draft(owner), draft(owner)
    for listing_id in (view.id, kept.id):
        post(client, draft_url(listing_id))
    post(client, URL, body())
    listings.delete_draft(user(owner), view.id)  # no commit: the event never runs
    now = timezone.now()
    assert retention.purge_estimates(now, dry_run=True) == 1
    assert PriceEstimate.objects.count() == 3
    assert retention.purge_estimates(now) == 1
    assert retention.purge_estimates(now + timedelta(days=91)) == 1
    assert PriceEstimate.objects.get().listing_id == kept.id


def test_the_preview_shows_the_latest_estimate_until_the_inputs_change(client, owner):
    view = draft(owner)
    preview = f"/api/v1/listings/{view.id}/preview"
    assert client.get(preview).json()["estimate"] is None
    asked = post(client, draft_url(view.id)).json()
    shown = client.get(preview).json()["estimate"]
    assert shown == {
        name: asked[name]
        for name in ("estimated_value", "currency", "model_version", "estimated_at")
    }
    assert PriceEstimate.objects.count() == 1  # reading computes nothing
    assert other_client().get(preview).status_code == 404
    # the Asking price is not a model input; the area is
    listings.apply_updates(user(owner), view.id, 2, {"asking_price": "5000000"})
    assert client.get(preview).json()["estimate"] == shown
    listings.apply_updates(user(owner), view.id, 3, {"area_value": "500", "area_unit": "sqyd"})
    assert client.get(preview).json()["estimate"] is None
