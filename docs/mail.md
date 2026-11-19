# Mail port

`apps/kernel/mail.py` is the only way to send email.

```python
from apps.kernel import mail

with transaction.atomic():
    ...  # the feature's own writes
    mail.send(mail.MailMessage(
        to=account_email,
        subject="Verify your email",
        body=f"Open {mail.email_link('/verify-email', {'token': token})}",
    ))
```

- `MailMessage` is frozen: one recipient `to`, `subject`, plain-text `body`, optional `html_body`. Each flow's story owns
  its copy; the port has no templates.
- `send` requires an open transaction (`RuntimeError` otherwise), validates, and only emits `MailRequested`
  (`mail.requested`). A rolled-back transaction therefore sends nothing. Invalid input is a `ValueError`: recipient
  must be one plain address (no whitespace, control characters, comma, semicolon, angle brackets; at most 254
  characters), subject 1 to 200 characters without control characters, bodies at most 100 000 characters, and the
  sender (`MAIL_FROM`) must hold no control characters.
- The kernel registers `mail.deliver` in `KernelConfig.ready`. After commit it submits delivery to the runtime
  executor's interactive lane (never the heavy lane, so AI load cannot drop mail, and never the request thread).
  With `MAIL_SEND_INLINE` (true in test settings) delivery is synchronous.
- Delivery uses `EmailMultiAlternatives` and `EMAIL_BACKEND`.
- **No retry.** A failure (including the backend raising) is logged by exception class only (`estate.mail`, event
  `mail.delivery_failed`), never changes the response, and is not retried: verification and reset flows let the user
  request a new link. There is no outbox or bounce handling.
- Nothing about a message (address, subject, body, link) is logged, audited or put in an error body.

## Caveats

- Queued mail is lost if the process stops before delivery: there is no drain on shutdown and no outbox.
- Interactive-lane latency under executor saturation is unbounded; delivery waits for a free worker.
- `html_body` is not escaped. Features must escape any user-controlled value they put in it.
- `send` raises `ValueError` synchronously for invalid input (and `RuntimeError` outside a transaction); only
  delivery failures are contained.
- Per-recipient and per-account abuse limits belong to the sending feature, through `kernel.ratelimit` scopes
  (`verification_email`, `password_reset_email`); the port does not throttle.
- `MAIL_FROM` is validated at prod start-up (`addr` or `Name <addr>`); `MAIL_USE_TLS` and `MAIL_USE_SSL` are exclusive,
  and `MAIL_USER` requires one of them.

## Links

`email_link(path, params=None)` builds an absolute URL from `FRONTEND_BASE_URL` through `frontend_url` (hostile paths
raise `ValueError`; the Host header is never used) and appends parameters URL-encoded in sorted order.

## Environments

| Settings | Backend | Notes |
|----------|---------|-------|
| local | SMTP to Mailpit (`MAIL_HOST` default `127.0.0.1`, `MAIL_PORT` 1025) | start-up fails unless `MAIL_HOST` is `127.0.0.1`, `localhost` or `::1` |
| test | in-memory (`django.core.mail.outbox`) | `MAIL_SEND_INLINE = True` |
| prod | SMTP from `MAIL_HOST`, `MAIL_PORT` (587), `MAIL_USER`, `MAIL_PASSWORD`, `MAIL_USE_TLS` (true) | `MAIL_HOST` and `MAIL_FROM` required; `MAIL_PASSWORD` is never logged |

## Viewing captured mail locally

`docker compose up -d --wait`, then open http://127.0.0.1:8025 (API: `/api/v1/messages`). To send one message end to
end: `DJANGO_SETTINGS_MODULE=config.settings.local uv run python scripts/mail_check.py`.

## Testing a feature that sends mail

Use `django.core.mail.outbox` with `django_capture_on_commit_callbacks(execute=True)`. `send` re-registers the kernel's
delivery handler if `events.clear()` removed it.
