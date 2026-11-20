from django.db import migrations

POLICIES = [
    (
        "admin_sign_in",
        5,
        900,
        "client",
        "Story 1.22: failed administrator sign-ins per 15 minutes; key by email and address",
    ),
    (
        "admin_sign_in_account",
        20,
        3600,
        "account",
        "Story 1.22: failed administrator sign-ins per hour per email, any address",
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
    dependencies = [("kernel", "0008_seed_email_change_policies")]

    operations = [migrations.RunPython(seed, unseed)]
