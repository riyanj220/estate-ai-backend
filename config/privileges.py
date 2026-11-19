"""Grants for the runtime database role: DML only, never DDL."""

import re

_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def role_exists(connection, role: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
        return cursor.fetchone() is not None


def grant_runtime_privileges(connection, role: str, append_only=()) -> None:
    if not _ROLE_RE.match(role):
        raise ValueError("Invalid database role name.")
    quote = connection.ops.quote_name
    r = quote(role)
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_schema()")
        schema = quote(cursor.fetchone()[0])
        cursor.execute(f"GRANT USAGE ON SCHEMA {schema} TO {r}")
        cursor.execute(f"REVOKE CREATE ON SCHEMA {schema} FROM {r}")
        cursor.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {r}"
        )
        cursor.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {r}")
        for table in append_only:
            cursor.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {schema}.{quote(table)} FROM {r}")
