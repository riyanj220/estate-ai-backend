"""The data migration of `first_published_at` (Story 3.4)."""

import importlib
from datetime import timedelta

from django.apps import apps
from django.db import connection

from apps.listings.models import PropertyListing, PublicationEvent
from tests.listings.support import make_account, ready_draft, seed_cap

migration = importlib.import_module("apps.listings.migrations.0017_first_published_at")


class Editor:
    pass


def test_backfill_takes_the_earliest_event_of_each_listing(db):
    seed_cap()
    owner = make_account()
    published, draft = ready_draft(owner), ready_draft(owner)
    first = PropertyListing.objects.get(id=published.id).created_at + timedelta(days=1)
    for at, kind in ((first + timedelta(days=9), "republish_free"), (first, "initial")):
        PublicationEvent.objects.create(
            account_id=owner.id, listing_id=published.id, kind=kind, counted=False, created_at=at
        )
    editor = Editor()
    editor.connection = connection

    migration.backfill(apps, editor)

    stored = dict(PropertyListing.objects.values_list("id", "first_published_at"))
    assert stored == {published.id: first, draft.id: None}
