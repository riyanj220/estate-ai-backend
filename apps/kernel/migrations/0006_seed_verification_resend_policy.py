from django.db import migrations


def seed(apps, schema_editor):
    Policy = apps.get_model("kernel", "RateLimitPolicy").objects.using(
        schema_editor.connection.alias
    )
    Policy.get_or_create(
        scope="verification_resend",
        defaults={
            "limit": 5,
            "window_seconds": 3600,
            "key_kind": "client",
            "note": "Story 1.16: verification link requests per hour per client",
        },
    )


def unseed(apps, schema_editor):
    apps.get_model("kernel", "RateLimitPolicy").objects.using(
        schema_editor.connection.alias
    ).filter(scope="verification_resend").delete()


class Migration(migrations.Migration):
    dependencies = [("kernel", "0005_audit_record")]

    operations = [migrations.RunPython(seed, unseed)]
