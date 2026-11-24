from django.db import migrations

H = 3600
MB = 1024 * 1024

# (scope, limit, window_seconds, key_kind, note). Caps have no window; the value is bytes.
POLICIES = [
    (
        "storage_cap_bytes_individual",
        200 * MB,
        None,
        "account",
        "Story 2.9: stored image bytes cap, Individual account (200 MB)",
    ),
    (
        "storage_cap_bytes_agency",
        1024 * MB,
        None,
        "account",
        "Story 2.9: stored image bytes cap, Agency account (1 GB)",
    ),
    ("image_upload", 30, H, "account", "Story 2.9: image uploads per hour per account"),
]


def seed(apps, schema_editor):
    Policy = apps.get_model("kernel", "RateLimitPolicy").objects.using(
        schema_editor.connection.alias
    )
    for scope, limit, window, kind, note in POLICIES:
        Policy.get_or_create(
            scope=scope,
            defaults={"limit": limit, "window_seconds": window, "key_kind": kind, "note": note},
        )


def unseed(apps, schema_editor):
    Policy = apps.get_model("kernel", "RateLimitPolicy").objects.using(
        schema_editor.connection.alias
    )
    for scope, limit, window, kind, note in POLICIES:  # only rows still at the seeded defaults
        Policy.filter(
            scope=scope, limit=limit, window_seconds=window, key_kind=kind, note=note
        ).delete()


class Migration(migrations.Migration):
    dependencies = [("kernel", "0011_media_assets")]

    operations = [migrations.RunPython(seed, unseed)]
