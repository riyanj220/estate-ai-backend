from django.db import DEFAULT_DB_ALIAS, migrations


def seed_karachi(apps, schema_editor):
    # Use the migrating connection: the runtime role has no rights until post_migrate.
    alias = schema_editor.connection.alias if schema_editor else DEFAULT_DB_ALIAS
    cities = apps.get_model("catalog", "City").objects.using(alias)
    # Idempotent: a row of any casing, edited or not, is left exactly as it is.
    if not cities.filter(name__iexact="karachi").exists():
        cities.create(name="Karachi")


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]

    operations = [migrations.RunPython(seed_karachi, migrations.RunPython.noop)]
