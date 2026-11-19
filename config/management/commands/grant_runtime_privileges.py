from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from config.privileges import grant_runtime_privileges, role_exists


class Command(BaseCommand):
    help = (
        "Grant DML (never DDL) privileges to the runtime database role. "
        "Append-only tables come from settings.RUNTIME_APPEND_ONLY_TABLES."
    )

    def add_arguments(self, parser):
        default = "migrator" if "migrator" in settings.DATABASES else "default"
        parser.add_argument("--database", default=default, help="Connection with grant rights.")

    def handle(self, *args, **options):
        connection = connections[options["database"]]
        role = settings.RUNTIME_DB_USER
        if not role_exists(connection, role):
            raise CommandError("Runtime database role does not exist.")
        grant_runtime_privileges(connection, role, settings.RUNTIME_APPEND_ONLY_TABLES)
        self.stdout.write("Runtime privileges granted.")
