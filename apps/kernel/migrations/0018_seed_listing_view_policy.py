from django.db import migrations

# (scope, limit, window_seconds, key_kind, note)
POLICIES = [
    ("listing_view", 300, 3600, "client", "Story 8.2: view beacons per hour per network"),
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
    dependencies = [("kernel", "0017_seed_messaging_policies")]

    operations = [migrations.RunPython(seed, unseed)]
