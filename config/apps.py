from django.apps import AppConfig
from django.conf import settings
from django.core.management.base import CommandError
from django.db.models.signals import post_migrate, pre_migrate


def _grant_after_migrate(sender, using, **kwargs):
    # post_migrate fires once per app; all tables exist by then, so one run is enough.
    if sender.label != "contenttypes":
        return
    from django.db import connections

    from config.privileges import grant_purge_privileges, grant_runtime_privileges, role_exists

    connection = connections[using]
    role = settings.RUNTIME_DB_USER
    purger = settings.PURGE_DB_USER
    if connection.settings_dict["USER"] in (role, purger):
        return
    if role_exists(connection, role):
        grant_runtime_privileges(
            connection, role, settings.RUNTIME_APPEND_ONLY_TABLES, settings.RUNTIME_COLUMN_UPDATES
        )
    if role_exists(connection, purger):
        grant_purge_privileges(connection, purger, settings.PURGE_ROLE_TABLES)


def _require_schema(sender, using, **kwargs):
    """Never migrate into another schema (on Supabase, `public` is what its web API exposes)."""
    if not settings.DB_SCHEMA or sender.label != "contenttypes":
        return
    from django.db import connections

    with connections[using].cursor() as cursor:
        cursor.execute("SELECT current_schema()")
        if cursor.fetchone()[0] != settings.DB_SCHEMA:
            raise CommandError(
                f"This connection does not resolve to the schema '{settings.DB_SCHEMA}'; "
                "run scripts/supabase_setup.py first."
            )


class ConfigAppConfig(AppConfig):
    name = "config"
    label = "config"

    def ready(self):
        pre_migrate.connect(_require_schema, dispatch_uid="config.require_schema")
        post_migrate.connect(_grant_after_migrate, dispatch_uid="config.grant_runtime")
