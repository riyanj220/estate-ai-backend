from django.db import migrations

# (scope, limit, window_seconds, key_kind, note)
POLICIES = [
    ("message_send", 60, 60, "account", "Story 7.1: Messages per minute per account"),
    ("conversation_new", 20, 86400, "account", "Story 7.1: new Conversations per day per account"),
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
    dependencies = [("kernel", "0016_seed_voice_search_policies")]

    operations = [migrations.RunPython(seed, unseed)]
