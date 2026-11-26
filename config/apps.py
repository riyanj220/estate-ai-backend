from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate


def _grant_after_migrate(sender, using, **kwargs):
    # post_migrate fires once per app; all tables exist by then, so one run is enough.
    if sender.label != "contenttypes":
        return
    from django.db import connections

    from config.privileges import grant_runtime_privileges, role_exists

    connection = connections[using]
    role = settings.RUNTIME_DB_USER
    if connection.settings_dict["USER"] == role or not role_exists(connection, role):
        return
    grant_runtime_privileges(
        connection, role, settings.RUNTIME_APPEND_ONLY_TABLES, settings.RUNTIME_COLUMN_UPDATES
    )


class ConfigAppConfig(AppConfig):
    name = "config"
    label = "config"

    def ready(self):
        post_migrate.connect(_grant_after_migrate, dispatch_uid="config.grant_runtime")
