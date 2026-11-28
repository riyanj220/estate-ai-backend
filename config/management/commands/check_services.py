"""Check that the configured database, media store and mail server work. Prints no secrets."""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from apps.kernel import storage
from apps.kernel.mail_backend import is_undeliverable


class Command(BaseCommand):
    help = (
        "Check the database connections and the media store (a test object is written, read and "
        "deleted). With --email ADDRESS, also send one test message."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", help="Send a test message to this address.")

    def handle(self, *args, **options):
        failed = []
        checks = [(f"database '{alias}'", lambda a=alias: self._database(a)) for alias in
                  connections]  # fmt: skip
        checks.append(("media store", self._storage))
        if settings.DB_SCHEMA:
            checks.append(("web API roles", self._api_roles))
        if options["email"]:
            checks.append(("mail", lambda: self._mail(options["email"])))
        for name, check in checks:
            try:
                self.stdout.write(f"OK    {name}: {check()}")
            except Exception as exc:  # report every check, then fail
                failed.append(name)
                # The class only: provider messages can echo hosts, users or addresses.
                detail = str(exc) if isinstance(exc, CommandError) else type(exc).__name__
                self.stdout.write(f"FAIL  {name}: {detail}")
        if failed:
            raise CommandError(f"{len(failed)} check(s) failed.")

    def _database(self, alias: str) -> str:
        connection = connections[alias]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_user, current_schema(), current_setting('server_version'), "
                "has_schema_privilege(current_schema(), 'CREATE')"
            )
            user, schema, version, can_create = cursor.fetchone()
            encrypted = bool(connection.connection.pgconn.ssl_in_use)
        if settings.DB_SCHEMA and schema != settings.DB_SCHEMA:
            raise CommandError(f"schema is '{schema}', expected '{settings.DB_SCHEMA}'")
        if alias == "default" and user == settings.RUNTIME_DB_USER and can_create:
            raise CommandError("the runtime role can create objects; it must not")
        return f"role {user}, schema {schema}, PostgreSQL {version}, SSL {encrypted}"

    def _api_roles(self) -> str:
        """Supabase's Data API roles must reach no table in `public` or the application's
        schema, and none of the application's tables may be in `public`."""
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.django_migrations') IS NOT NULL")
            if cursor.fetchone()[0]:
                raise CommandError("application tables exist in 'public'; they must not")
            cursor.execute(
                "SELECT r.rolname, n.nspname, c.relname FROM pg_roles r "
                "CROSS JOIN pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE r.rolname IN ('anon', 'authenticated') AND n.nspname IN ('public', %s) "
                "AND c.relkind IN ('r', 'p', 'v', 'm') "
                "AND has_schema_privilege(r.oid, n.oid, 'USAGE') "
                "AND has_any_column_privilege(r.oid, c.oid, 'SELECT, INSERT, UPDATE') LIMIT 1",
                [settings.DB_SCHEMA],
            )
            found = cursor.fetchone()
        if found:
            raise CommandError(f"role {found[0]} can reach {found[1]}.{found[2]}")
        return "anon and authenticated can reach no table"

    def _storage(self) -> str:
        store = storage.get_storage()
        key, data = storage.new_key(), b"estate-ai storage check"
        store.put(key, data)
        try:
            if not store.exists(key) or store.get(key) != data:
                raise CommandError("the object read back differs")
            next(store.iter_old_keys(0), None)  # listing is allowed (retention needs it)
        finally:
            store.delete(key)
        if store.exists(key):
            raise CommandError("the object was not deleted")
        return f"{settings.MEDIA_BACKEND}: write, read, list and delete work"

    def _mail(self, address: str) -> str:
        if settings.EMAIL_BACKEND.endswith("ReservedSafeSMTPBackend") and is_undeliverable(address):
            raise CommandError("that is a reserved address; nothing is sent to it")
        sent = send_mail(
            "EstateAI test message",
            "If you can read this, outgoing mail works.",
            settings.DEFAULT_FROM_EMAIL,
            [address],
        )
        if sent != 1:
            raise CommandError("the message was not accepted")
        return "test message accepted by the mail server"
