#!/usr/bin/env bash
# Creates the migrator and runtime roles and the pgvector and btree_gist extensions.
# Runs from the Postgres image's init directory, and from CI against a service
# container (set PGHOST, PGPORT and PGPASSWORD there). Idempotent.
#
# Required env: POSTGRES_USER, POSTGRES_DB, MIGRATOR_USER, MIGRATOR_PASSWORD,
# RUNTIME_USER, RUNTIME_PASSWORD.
set -euo pipefail

: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"
: "${MIGRATOR_USER:?}" "${MIGRATOR_PASSWORD:?}" "${RUNTIME_USER:?}" "${RUNTIME_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" \
  -v m_user="$MIGRATOR_USER" -v m_pw="$MIGRATOR_PASSWORD" \
  -v r_user="$RUNTIME_USER" -v r_pw="$RUNTIME_PASSWORD" <<'SQL'
-- Migrator: owns the schema, may create test databases, runs migrations.
SELECT format('CREATE ROLE %I LOGIN CREATEDB PASSWORD %L', :'m_user', :'m_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'m_user') \gexec
-- Runtime: serves requests; LOGIN only, no CREATE anywhere.
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L',
              :'r_user', :'r_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'r_user') \gexec

-- Re-running rotates the passwords of roles that already exist.
SELECT format('ALTER ROLE %I PASSWORD %L', :'m_user', :'m_pw') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'r_user', :'r_pw') \gexec

SELECT format('ALTER DATABASE %I OWNER TO %I', :'db', :'m_user') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'db', :'r_user') \gexec
-- Test databases are created by the migrator from template1; keep them connectable.

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;
\connect template1
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;
SQL
