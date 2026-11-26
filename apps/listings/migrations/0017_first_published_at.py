"""Story 3.4: store the first publication time on the Listing (public search orders by it) and
index it for published rows. Existing Listings get it from their earliest publication event."""

import django.db.models.functions.comparison
from django.db import migrations, models


def backfill(apps, schema_editor):
    db = schema_editor.connection.alias
    Listing = apps.get_model("listings", "PropertyListing")
    first = (
        apps.get_model("listings", "PublicationEvent")
        .objects.filter(
            account_id=models.OuterRef("owner_account_id"), listing_id=models.OuterRef("id")
        )
        .order_by("created_at")
        .values("created_at")[:1]
    )
    Listing.objects.using(db).filter(first_published_at__isnull=True).update(
        first_published_at=models.Subquery(first)
    )


class Migration(migrations.Migration):
    dependencies = [("listings", "0016_identity_fingerprint_v10")]

    operations = [
        migrations.AddField(
            model_name="propertylisting",
            name="first_published_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="propertylisting",
            index=models.Index(
                models.OrderBy(
                    django.db.models.functions.comparison.Coalesce(
                        "first_published_at", "created_at"
                    ),
                    descending=True,
                ),
                models.OrderBy(models.F("id"), descending=True),
                condition=models.Q(("restricted", False), ("status", "published")),
                name="listings_public_newest_idx",
            ),
        ),
    ]
