from django.db import migrations

# (scope, limit, window_seconds, key_kind, note)
POLICIES = [
    (
        "ai_voice_visitor",
        10,
        60,
        "client",
        "Story 6.4: voice transcriptions per minute per Visitor",
    ),
    (
        "ai_voice_account",
        30,
        3600,
        "account",
        "Story 6.4: voice transcriptions per hour per account",
    ),
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
    dependencies = [("kernel", "0015_aibudgetledger")]

    operations = [migrations.RunPython(seed, unseed)]
