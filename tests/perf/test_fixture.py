"""The generator of the large fixture, on a small count (fast: part of the default run)."""

from django.db import transaction
from django.test import Client

from apps.identity import selectors as identity
from apps.listings import selectors
from apps.listings.models import ListingImage, PropertyListing
from tests.perf import fixture


def snapshot():
    return list(
        PropertyListing.objects.order_by("id").values_list(
            "id", "status", "title", "owner_account_id", "terms__asking_price", "created_at"
        )
    )


def test_the_fixture_is_published_imageless_and_distinct_from_sample_data(db):
    made = fixture.generate(60)

    public = set(selectors.public_listings().values_list("id", flat=True))
    assert public == set(made.published_ids) and len(public) == 60
    assert len(made.hidden_ids) == 6
    assert not ListingImage.objects.exists()
    assert not identity.sample_account_ids()
    assert all(row[2].startswith(fixture.TITLE_PREFIX) for row in snapshot())
    response = Client().get("/api/v1/public/listings?limit=50")
    assert response.status_code == 200 and len(response.json()["results"]) == 50


def test_the_same_arguments_make_the_same_rows(db):
    runs = []
    for _ in range(2):
        with transaction.atomic():
            made = fixture.generate(40)
            runs.append((made, snapshot()))
            transaction.set_rollback(True)
    assert runs[0] == runs[1]
