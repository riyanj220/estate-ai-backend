# Running locally against real services

The app still runs on your laptop (`http://localhost:3000`), but data goes to Supabase PostgreSQL, uploaded images to a
private Supabase Storage bucket, and email through a real SMTP account. This is not a production deployment. Normal local
development is unchanged: it keeps using `.env`, Docker, `media/` and the mail catcher.

All commands run in `estate-ai-backend`. Everything secret goes into `.env.hosted`, which git ignores. Never commit or
share it.

## 1. Create the accounts and resources (you, in the browser)

1. **Supabase project** (<https://supabase.com>): create a new, dedicated project. Choose a region near you. Keep the
   database password; it is asked for in step 3 and is not stored anywhere in this repository.
2. **Close the Data API.** This app never uses it. Open the Data API integration in the dashboard and turn **Enable Data
   API** off. Do not add the `estate` schema to any "exposed schemas" list.
3. **Database connection**: click **Connect** and choose **Session pooler**. Note the host
   (`aws-N-<region>.pooler.supabase.com`) and the project ref (the part after `postgres.` in the user name).
4. **Storage**: create a bucket and leave **Public bucket off**. Add no access policies. In the Storage S3 settings,
   note the endpoint and region, and create an **S3 access key**. That key has full access to every bucket of the
   project, so treat it like a password.
5. **Email (default: Gmail)**: create a new Gmail account used only for this app. Turn on **2-Step Verification**, then
   create an **app password** at <https://myaccount.google.com/apppasswords>. Changing the account password revokes it.

## 2. Fill in `.env.hosted`

```bash
cp .env.hosted.example .env.hosted
chmod 600 .env.hosted
```

Replace every `<...>`. For `DJANGO_SECRET_KEY` and the database role passwords (`MIGRATOR_PASSWORD`, `RUNTIME_PASSWORD`,
`PURGE_PASSWORD`), generate your own random values, one each:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Leave `PURGE_PASSWORD` empty if you do not want the history-deleting role. For real AI answers set `AI_PROVIDER=gemini`
and `GEMINI_API_KEY`; hosted mode does not read `.env`. The file is the whole configuration: shell variables with the same
names are ignored.

## 3. Set up and run

| Step | Command |
| --- | --- |
| Install dependencies | `uv sync --frozen` |
| Create roles, schema, extensions (asks for the database password) | `uv run python scripts/supabase_setup.py` |
| Create the tables | `scripts/hosted.sh migrate --database=migrator` |
| Check database, bucket and API roles; send a test email | `scripts/hosted.sh check_services --email you@your-real-address` |
| Register the price model | `scripts/hosted.sh register_valuation_model` |
| Build the assistant's knowledge index | `scripts/hosted.sh rebuild_knowledge_index` |
| Create an administrator | `scripts/hosted.sh provision_admin create --email you@your-real-address` |
| Optional sample marketplace (see below) | `scripts/hosted.sh seed_sample_data` |
| Start the backend (stop the normal one first: same port 8000) | `scripts/hosted.sh serve` |
| Start the frontend (in `estate-ai-frontend`, as usual) | `npm run dev` or `AI_FEATURES=1 npm run dev` |

The setup script is safe to run again; it also sets the role passwords to the ones in the file.

**Sample data** is refused unless you opt in: set `SEED_ALLOWED=true` in `.env.hosted`, run the command, set it back to
`false`. Choose a strong password when asked: these accounts are then in a real database. Their addresses end in
`.invalid`, and hosted mode never sends mail to such reserved addresses. Remove them with
`scripts/hosted.sh seed_sample_data --reset --yes` (also needs the opt-in).

`serve` listens on this laptop only. Run one server process, and restart it after code changes.

## 4. Scheduled jobs (optional)

`scripts/hosted_jobs.sh` runs `run_flags` and `run_retention` once. Run it by hand, or schedule it. Nothing is installed
for you; replace `/path/to/estate-ai-backend`.

Cron (`crontab -e`), daily at 21:00. `PATH` must contain `uv` (see `which uv`):

```cron
PATH=/home/YOU/.local/bin:/usr/bin:/bin
0 21 * * * /path/to/estate-ai-backend/scripts/hosted_jobs.sh >> $HOME/estate-hosted-jobs.log 2>&1
```

Or a systemd user timer, which also catches up after the laptop was off. Create
`~/.config/systemd/user/estate-jobs.service`:

```ini
[Service]
Type=oneshot
Environment=PATH=%h/.local/bin:/usr/bin:/bin
ExecStart=/path/to/estate-ai-backend/scripts/hosted_jobs.sh
```

and `~/.config/systemd/user/estate-jobs.timer`:

```ini
[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

Then `systemctl --user daemon-reload && systemctl --user enable --now estate-jobs.timer`.

The audit purge stays manual: `scripts/hosted.sh purge_audit_records --dry-run` (needs `PURGE_PASSWORD`).

## 5. Later: your own domain (Brevo)

The mail settings are plain SMTP. Once you have a domain, verify it in Brevo and change only the mail lines:
`MAIL_HOST=smtp-relay.brevo.com`, `MAIL_PORT=587`, `MAIL_USER` and `MAIL_PASSWORD` from Brevo's SMTP settings, and
`MAIL_FROM` an address on that domain.

## 6. Good to know

- **Free plan** (<https://supabase.com/pricing>, checked November 2026): 500 MB database, 1 GB file storage, 5 GB egress
  plus 5 GB cached egress, 50 MB per file, 2 active projects, no automatic backups, and a project is **paused after one
  week without activity** (restore it in the dashboard). Gmail also limits sending; it is for a demo, not bulk mail.
- **One bucket belongs to one database.** Retention deletes stored files that have no database record, so never point
  two databases at the same bucket. If you recreate the database, empty the bucket too.
- Your local data and `media/` files are not copied to the hosted services.
- Supabase offers PostgreSQL 17 (local development uses 18). All migrations were run on a Supabase 17 image.
- Do not use the **transaction pooler** (port 6543); hosted mode refuses it. The direct connection is IPv6 only on the
  free plan, so use the session pooler.
- SSL is always on. `DB_SSLMODE=require` encrypts but does not verify the server; for that, download the certificate in
  the database settings and set `DB_SSLMODE=verify-full` and `DB_SSLROOTCERT`.
- Tables live in the `estate` schema, never in `public`. The setup script also revokes the rights of Supabase's `anon`,
  `authenticated` and `service_role` roles on `estate` and `public`. `check_services` verifies the result.
- Pages are slower than locally: every database query and image travels to Supabase.
- Tests refuse to run against a database that is not on this machine.

## If something fails

| Message | Do |
| --- | --- |
| `Environment variable X is missing or not filled in` | Fill `X` in `.env.hosted` |
| `does not resolve to the schema 'estate'` | Run `scripts/supabase_setup.py` |
| Setup: password authentication failed | Use the database password of the project, not your Supabase login |
| Database checks fail after a quiet week | The project is paused: restore it in the dashboard |
| `media store` fails | Check endpoint, region, bucket name and S3 keys |
| `mail` fails | Gmail: 2-Step Verification on, app password without spaces, `MAIL_FROM` is the same address |

Sources: [connecting](https://supabase.com/docs/guides/database/connecting-to-postgres),
[securing the Data API](https://supabase.com/docs/guides/api/securing-your-api),
[S3 keys](https://supabase.com/docs/guides/storage/s3/authentication),
[S3 compatibility](https://supabase.com/docs/guides/storage/s3/compatibility),
[Gmail app passwords](https://support.google.com/accounts/answer/185833).
