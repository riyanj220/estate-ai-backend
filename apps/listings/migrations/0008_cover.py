from django.db import migrations, models

# Story 2.9 positions were 1-based and could have gaps. Renumber them 0..n-1 per Listing (in the
# old order) and make the first image of each Listing the cover. Reversible: positions go back to
# 1..n (the gap-free form is a valid 2.9 state) and the cover flag is dropped with the column.
FORWARD = """
UPDATE listings_listingimage AS li
SET position = ranked.rank - 1, is_cover = (ranked.rank = 1)
FROM (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY listing_id ORDER BY position, created_at, id) AS rank
    FROM listings_listingimage
) AS ranked
WHERE li.id = ranked.id
"""
BACKWARD = """
UPDATE listings_listingimage AS li
SET position = ranked.rank
FROM (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY listing_id ORDER BY position, id) AS rank
    FROM listings_listingimage
) AS ranked
WHERE li.id = ranked.id
"""


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0007_images"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="listingimage", name="listings_image_position_gte_1"
        ),
        migrations.RemoveConstraint(
            model_name="listingimage", name="listings_image_position_unique"
        ),
        migrations.AddField(
            model_name="listingimage",
            name="is_cover",
            field=models.BooleanField(default=False),
        ),
        migrations.RunSQL(FORWARD, BACKWARD),
        migrations.AddConstraint(
            model_name="listingimage",
            constraint=models.CheckConstraint(
                condition=models.Q(("position__gte", 0)), name="listings_image_position_gte_0"
            ),
        ),
        migrations.AddConstraint(
            model_name="listingimage",
            constraint=models.UniqueConstraint(
                fields=("listing", "position"),
                name="listings_image_position_unique",
                deferrable=models.Deferrable.DEFERRED,
            ),
        ),
        migrations.AddConstraint(
            model_name="listingimage",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_cover", True)),
                fields=("listing",),
                name="listings_image_one_cover",
            ),
        ),
    ]
