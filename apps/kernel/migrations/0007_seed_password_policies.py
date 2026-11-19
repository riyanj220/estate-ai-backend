from django.db import migrations

H = 3600

POLICIES = [
    (
        "password_reset_request",
        5,
        H,
        "client",
        "Story 1.18: password reset requests per hour per client",
    ),
    (
        "password_reset_confirm",
        20,
        H,
        "client",
        "Story 1.18: password reset confirmations per hour per client",
    ),
    (
        "password_change_failed",
        5,
        900,
        "account",
        "Story 1.18: failed current-password checks per 15 minutes per account",
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
    dependencies = [("kernel", "0006_seed_verification_resend_policy")]

    operations = [migrations.RunPython(seed, unseed)]
