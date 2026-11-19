from django.db import migrations

H, D, M = 3600, 86400, 60

# (scope, limit, window_seconds, key_kind, note). NFR-10 defaults; the owning story applies each.
POLICIES = [
    ("registration", 10, H, "client", "NFR-10: registrations per hour per client"),
    ("verification_email", 5, H, "account", "NFR-10: verification emails per hour per account"),
    ("password_reset_email", 5, H, "account", "NFR-10: reset emails per hour per account"),
    ("messages", 60, M, "account", "NFR-10: Messages per minute per account"),
    ("conversations_new", 20, D, "account", "NFR-10: new Conversations per day per account"),
    ("favourites", 500, None, "account", "NFR-10: favourites cap per account"),
    ("reports", 10, D, "account", "NFR-10: Reports per day per account"),
    ("support_requests", 5, D, "account", "NFR-10: support requests per day per account"),
    ("open_drafts", 20, None, "account", "NFR-10: open Drafts cap per account"),
    ("assistant_messages", 30, H, "account", "NFR-10: Assistant messages per hour per account"),
    (
        "ai_search_visitor",
        10,
        M,
        "client",
        "NFR-10: natural-language and voice searches per Visitor",
    ),
    (
        "ai_search_account",
        30,
        H,
        "account",
        "NFR-10: natural-language and voice searches per account",
    ),
    ("estimates", 20, H, "account", "FR-42: estimate requests per hour per account"),
    (
        "sign_in",
        5,
        900,
        "client",
        "FR-3: failed sign-ins per 15 minutes; key by account and address",
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


class Migration(migrations.Migration):
    dependencies = [("kernel", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
