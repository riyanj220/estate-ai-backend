"""Manual check: send one message through the mail port under local settings and read it back
from Mailpit's HTTP API. Needs `docker compose up -d --wait` and a populated .env.

    DJANGO_SETTINGS_MODULE=config.settings.local uv run python scripts/mail_check.py
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.db import transaction  # noqa: E402

from apps.kernel import mail, runtime  # noqa: E402

ui = f"http://127.0.0.1:{os.environ.get('MAIL_UI_PORT', '8025')}"
subject = f"mail-check {uuid.uuid4()}"
query = urllib.parse.urlencode({"query": f'subject:"{subject}"'})


def call(path, method="GET", body=None):
    request = urllib.request.Request(f"{ui}{path}", method=method, data=body)  # noqa: S310
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        raw = response.read()
    return json.loads(raw) if raw[:1] in (b"{", b"[") else {}


try:
    with transaction.atomic():
        mail.send(
            mail.MailMessage(
                to="manual-check@example.org",
                subject=subject,
                body=f"Link: {mail.email_link('/verify-email', {'token': 'demo'})}",
            )
        )
    for _ in range(50):
        found = call(f"/api/v1/search?{query}").get("messages", [])
        if found:
            call("/api/v1/messages", "DELETE", json.dumps({"IDs": [found[0]["ID"]]}).encode())
            print(f"OK: captured by Mailpit at {ui} (from {settings.DEFAULT_FROM_EMAIL}); removed")
            break
        time.sleep(0.1)
    else:
        sys.exit("FAILED: message not found in Mailpit")
finally:
    runtime.shutdown()
