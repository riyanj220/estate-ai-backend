"""Create the application's database roles, schema and extensions on Supabase. Safe to repeat.

    uv run python scripts/supabase_setup.py

Reads the host, project ref and the role passwords from `.env.hosted`, and asks for the password
of the `postgres` user (the database password chosen when the project was created), which is never
stored. Passwords are sent only as SCRAM hashes and are never printed. Running it again sets the
role passwords to those in the file.

What it does: the roles `migrator` (owns the tables, runs migrations), `runtime` (the app; no
DDL) and, if PURGE_PASSWORD is set, `purger`; a schema of their own (default `estate`), so that
no table is ever in `public`, which Supabase's web API exposes; the extensions `vector` and
`btree_gist`; and it revokes the web API roles' (`anon`, `authenticated`, `service_role`) rights
on that schema and on `public`. Turning the Data API off in the dashboard is still advised.
Table rights for `runtime` and `purger` are granted by `migrate`.
"""

import getpass
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.env import BASE_DIR, load_dotenv  # noqa: E402

EXTENSIONS = ("vector", "btree_gist")
MIN_PASSWORD = 32
# The roles Supabase's web API (the Data API) acts as. The application never uses them.
API_ROLES = ("anon", "authenticated", "service_role")


def apply(conn, *, schema: str, roles: dict[str, str]) -> None:
    """`roles` maps "migrator", "runtime" and optionally "purger" (role names) to passwords, in
    that order. `conn` is an administrator that may create roles (`postgres` on Supabase)."""
    names = list(roles)
    migrator, others = names[0], names[1:]
    schema_id = sql.Identifier(schema)
    with conn.transaction(), conn.cursor() as cursor:
        for name, password in roles.items():
            hashed = conn.pgconn.encrypt_password(
                password.encode(), name.encode(), b"scram-sha-256"
            ).decode()
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [name])
            verb = sql.SQL("ALTER ROLE" if cursor.fetchone() else "CREATE ROLE")
            cursor.execute(
                sql.SQL("{} {} WITH LOGIN PASSWORD {}").format(
                    verb, sql.Identifier(name), sql.Literal(hashed)
                )
            )
        cursor.execute("CREATE SCHEMA IF NOT EXISTS extensions")
        for extension in EXTENSIONS:
            cursor.execute(
                sql.SQL("CREATE EXTENSION IF NOT EXISTS {} SCHEMA extensions").format(
                    sql.Identifier(extension)
                )
            )
        cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema_id))
        cursor.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(schema_id))
        # The grant option lets `migrate` (as the migrator) pass USAGE on to the other roles.
        cursor.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO {} WITH GRANT OPTION").format(
                schema_id, sql.Identifier(migrator)
            )
        )
        for name in names:
            role = sql.Identifier(name)
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA extensions TO {}").format(role))
            cursor.execute(
                sql.SQL("ALTER ROLE {} SET search_path = {}, extensions, public").format(
                    role, schema_id
                )
            )
        _close_data_api(cursor, schema_id)
        for name in others:
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema_id, sql.Identifier(name))
            )


def _close_data_api(cursor, schema_id: sql.Identifier) -> None:
    """Give the API roles nothing: none on the application's schema, and none on what `public`
    holds now or what `postgres` creates there later (Supabase's documented revokes)."""
    cursor.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", [list(API_ROLES)])
    present = [sql.Identifier(row[0]) for row in cursor.fetchall()]
    if not present:
        return
    roles = sql.SQL(", ").join(present)
    cursor.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(schema_id, roles))
    for objects in ("TABLES", "SEQUENCES"):
        cursor.execute(
            sql.SQL("REVOKE ALL ON ALL {} IN SCHEMA public FROM {}").format(sql.SQL(objects), roles)
        )
    for objects in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        cursor.execute(
            sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON {} FROM {}").format(
                sql.SQL(objects), roles
            )
        )


def _setting(name: str, default: str = "") -> str:
    value = os.environ.get(name) or default
    if not value or "<" in value:
        raise SystemExit(f"Fill in {name} in .env.hosted first.")
    return value


def main() -> None:
    path = Path(os.environ.get("HOSTED_ENV_FILE") or BASE_DIR / ".env.hosted")
    if not path.is_file():
        raise SystemExit("No .env.hosted file: copy .env.hosted.example and fill it in.")
    load_dotenv(path, override=True)
    host = _setting("POSTGRES_HOST")
    user = "postgres"
    if host.endswith(".pooler.supabase.com"):
        user += "." + _setting("SUPABASE_PROJECT_REF")
    roles = {
        os.environ.get("MIGRATOR_USER") or "migrator": _setting("MIGRATOR_PASSWORD"),
        os.environ.get("RUNTIME_USER") or "runtime": _setting("RUNTIME_PASSWORD"),
    }
    if os.environ.get("PURGE_PASSWORD"):
        roles[os.environ.get("PURGE_USER") or "purger"] = _setting("PURGE_PASSWORD")
    if any(len(password) < MIN_PASSWORD for password in roles.values()):
        raise SystemExit(f"Role passwords must be at least {MIN_PASSWORD} characters.")
    options = {"sslmode": os.environ.get("DB_SSLMODE") or "require"}
    if os.environ.get("DB_SSLROOTCERT"):
        options["sslrootcert"] = os.environ["DB_SSLROOTCERT"]
    admin_password = getpass.getpass("Password of the Supabase 'postgres' database user: ")
    try:
        with psycopg.connect(
            host=host,
            port=int(os.environ.get("POSTGRES_PORT") or 5432),
            dbname=os.environ.get("POSTGRES_DB") or "postgres",
            user=user,
            password=admin_password,
            connect_timeout=15,
            autocommit=True,
            **options,
        ) as conn:
            apply(conn, schema=os.environ.get("DB_SCHEMA") or "estate", roles=roles)
    except psycopg.Error as exc:
        # The server's primary message names objects, never passwords.
        reason = exc.diag.message_primary or type(exc).__name__
        raise SystemExit(f"Failed: {reason}") from None
    print("Done. Roles:", ", ".join(roles))
    print("Next: scripts/hosted.sh migrate --database=migrator")


if __name__ == "__main__":
    main()
