from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from config.privileges import grant_purge_privileges, grant_runtime_privileges, role_exists


class Command(BaseCommand):
    help = (
        "Grant DML (never DDL) privileges to the runtime database role, and SELECT and DELETE "
        "on settings.PURGE_ROLE_TABLES to the purge role if it exists. "
        "Append-only tables come from settings.RUNTIME_APPEND_ONLY_TABLES, the columns "
        "they may still update from settings.RUNTIME_COLUMN_UPDATES."
    )

    def add_arguments(self, parser):
        default = "migrator" if "migrator" in settings.DATABASES else "default"
        parser.add_argument("--database", default=default, help="Connection with grant rights.")

    def handle(self, *args, **options):
        connection = connections[options["database"]]
        role = settings.RUNTIME_DB_USER
        if not role_exists(connection, role):
            raise CommandError("Runtime database role does not exist.")
        grant_runtime_privileges(
            connection, role, settings.RUNTIME_APPEND_ONLY_TABLES, settings.RUNTIME_COLUMN_UPDATES
        )
        self.stdout.write("Runtime privileges granted.")
        if role_exists(connection, settings.PURGE_DB_USER):
            grant_purge_privileges(connection, settings.PURGE_DB_USER, settings.PURGE_ROLE_TABLES)
            self.stdout.write("Purge privileges granted.")
        else:
            self.stdout.write("Purge database role does not exist: no purge privileges granted.")
