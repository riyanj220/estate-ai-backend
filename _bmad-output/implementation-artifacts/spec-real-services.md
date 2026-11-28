# Spec: run locally against real hosted services

Status: built, not yet tried against a real Supabase project or mail account. Branch `story/real-services`.

## Goal

The developer can run the app on the laptop against Supabase PostgreSQL, Supabase Storage and real SMTP, without
changing local development, tests or CI. Not a production deployment. User guide: `docs/real-services.md`.

## What was built

| Area | Where |
| --- | --- |
| Settings, whole configuration from `.env.hosted` | `config/settings/hosted.py`, `.env.hosted.example` |
| S3 adapter of the storage port (AD-15), chosen by `MEDIA_BACKEND` | `apps/kernel/storage_s3.py`, `storage.get_storage` |
| SMTP backend that never mails reserved domains | `apps/kernel/mail_backend.py` |
| Roles, schema, extensions, Data API revokes (idempotent) | `scripts/supabase_setup.py` |
| Connection, storage, API-role and mail check | `check_services` management command |
| Refuse to migrate into the wrong schema | `config/apps.py` (`DB_SCHEMA`, empty elsewhere) |
| Wrapper and jobs | `scripts/hosted.sh`, `scripts/hosted_jobs.sh` |
| Test guards | `tests/conftest.py` (loopback database only), `hosted.py` refuses pytest and `test` |

New dependencies: `boto3` (imported only in kernel, now an import contract), `psycopg[pool]`.

## Decisions

1. Tables go in their own `estate` schema (role `search_path`), so nothing is in `public`, which Supabase's Data API
   exposes. The guide also turns the Data API off, and setup revokes `anon`, `authenticated`, `service_role`.
2. `migrator` gets `USAGE, CREATE ... WITH GRANT OPTION` on the schema instead of owning it or the database: Supabase's
   `postgres` is not a superuser and the database is not ours to reassign. No `CREATEDB`.
3. Session pooler (IPv4). Transaction pooler refused. SSL `require` by default, `verify-full` supported.
4. A small connection pool for the runtime alias only (`psycopg-pool`), to avoid a TLS handshake per request.
5. Media is read and written whole: one object is one processed image, so no streaming was added. Provider errors are
   `OSError`s, so an unreachable store makes an image "not found" and a failed upload roll back. Deletes stay after
   commit; retention lists the bucket for strays; usage accounting is in the database and unchanged.
6. Hosted mode discards same-named shell variables, so a local `.env` exported into the shell cannot leak in.
7. Server bound to loopback with `ALLOWED_HOSTS` fixed to localhost, so non-Secure cookies are acceptable.
8. Gmail app password is the documented mail default; Brevo later with a domain. Settings stay generic SMTP.
9. Mail to `.invalid`, `.test`, `.example`, `localhost` and `example.com/net/org` is dropped with a log line.
10. Sample data needs `SEED_ALLOWED=true` (existing mechanism).

## Verification

- Throwaway `supabase/postgres:17.12.0.004` with SSL, a versitygw S3 server and a Mailpit with STARTTLS, under the
  hosted settings: setup twice, migrate twice, `check_services` with mail, model, knowledge index, seed (147 objects),
  reseed, flags, retention, purge, serving an image through the media endpoint, reset (0 objects, 0 bytes of usage).
- MinIO images could not be pulled; versitygw was used.
- Full backend suite on a throwaway PostgreSQL 17 (`pgvector/pgvector:0.8.7-pg17-trixie`): 6629 passed, 5 skipped.
  On the local PostgreSQL 18: 6628 passed, 1 failed (contact-regex guard, a new pattern in `hosted.py`); fixed, then
  `-m guard` (572 passed) and the hosted and S3 tests passed. Ruff, import contracts and `makemigrations --check` clean.
- Automated tests use an in-memory S3 fake and never touch the network.

Not verifiable without the user's accounts: the real pooler login (`role.projectref`), the real `postgres` user's rights
in the setup script, real Supabase Storage S3 behaviour, Gmail delivery, and speed over the internet.
