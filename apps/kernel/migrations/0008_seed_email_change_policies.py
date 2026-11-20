from django.db import migrations

H = 3600

POLICIES = [
    (
        "email_change_request",
        5,
        H,
        "account",
        "Story 1.20: email change requests per hour per account",
    ),
    (
        "email_change_confirm",
        20,
        H,
        "client",
        "Story 1.20: email change confirmations per hour per client",
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
    dependencies = [("kernel", "0007_seed_password_policies")]

    operations = [migrations.RunPython(seed, unseed)]
