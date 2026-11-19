"""Mail port (AD-18).

A feature calls `send(message)` inside its transaction. The port only emits `MailRequested`,
so a rolled-back transaction sends nothing; the kernel's own subscriber delivers after commit
through Django's email backend, on the runtime executor's interactive lane so SMTP latency never
holds the request thread. Delivery is not retried: flows that send mail let the user ask for a new
link. Nothing about a message (address, subject, body, link) is ever logged.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction

from apps.kernel import runtime
from apps.kernel.events import DomainEvent, emit
from apps.kernel.links import frontend_url

logger = logging.getLogger("estate.mail")

MAX_ADDRESS_LENGTH = 254
MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 100_000

_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_ADDRESS = re.compile(r"^[^\s@<>,;:\"()\[\]\\]+@[^\s@<>,;:\"()\[\]\\]+$")


@dataclass(frozen=True)
class MailMessage:
    to: str
    subject: str
    body: str
    html_body: str | None = None


@dataclass(frozen=True)
class MailRequested(DomainEvent):
    name = "mail.requested"
    message: MailMessage


def _has_control(value: str) -> bool:
    return any(unicodedata.category(ch) in _UNSAFE_CATEGORIES for ch in value)


def _valid_domain(domain: str) -> bool:
    return domain.isascii() and all(domain.split("."))


def validate(message: MailMessage) -> None:
    """Raise ValueError if the message could inject headers or exceeds the limits."""
    for field in ("to", "subject", "body"):
        if not isinstance(getattr(message, field), str):
            raise ValueError(f"{field} must be text")
    if message.html_body is not None and not isinstance(message.html_body, str):
        raise ValueError("html_body must be text")
    to = message.to
    if (
        not to
        or len(to) > MAX_ADDRESS_LENGTH
        or _has_control(to)
        or not _ADDRESS.fullmatch(to)
        or not _valid_domain(to.rsplit("@", 1)[1])
    ):
        raise ValueError("recipient must be a single plain email address")
    if not message.subject or len(message.subject) > MAX_SUBJECT_LENGTH:
        raise ValueError(f"subject must be 1 to {MAX_SUBJECT_LENGTH} characters")
    if _has_control(message.subject):
        raise ValueError("subject must not contain control characters")
    if not message.body:
        raise ValueError("body must not be empty")
    sender = settings.DEFAULT_FROM_EMAIL
    if not sender or _has_control(sender):
        raise ValueError("sender is not a valid header value")
    for body in (message.body, message.html_body or ""):
        if len(body) > MAX_BODY_LENGTH:
            raise ValueError(f"body must be at most {MAX_BODY_LENGTH} characters")


def send(message: MailMessage) -> None:
    """Queue a message for delivery after the surrounding transaction commits."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("mail.send requires an open transaction")
    validate(message)
    # Imported here because the subscribe call must live in subscribers.py (convention test).
    from apps.kernel.subscribers import ensure_subscribed

    ensure_subscribed()
    emit(MailRequested(message=message))


def deliver(event: MailRequested) -> None:
    """Subscriber body: hand the message to the executor, or deliver inline in tests."""
    message = event.message
    if settings.MAIL_SEND_INLINE:
        _deliver(message)
        return
    try:
        runtime.submit_interactive(_deliver, message)
    except Exception as exc:
        _log_failure(exc)


def _deliver(message: MailMessage) -> None:
    try:
        mail = EmailMultiAlternatives(
            subject=message.subject,
            body=message.body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[message.to],
        )
        if message.html_body is not None:
            mail.attach_alternative(message.html_body, "text/html")
        mail.send(fail_silently=False)
    except Exception as exc:
        _log_failure(exc)


def _log_failure(exc: Exception) -> None:
    # Class name only: exception text from SMTP libraries can echo the address.
    logger.error(
        "mail delivery failed",
        extra={"event": "mail.delivery_failed", "exception_class": type(exc).__name__},
    )


def email_link(path: str, params: dict[str, str] | None = None) -> str:
    """Absolute frontend URL for an email; query values are URL-encoded in sorted order."""
    url = frontend_url(path)
    if params and not all(isinstance(k, str) and isinstance(v, str) for k, v in params.items()):
        raise ValueError("link parameters must be strings")
    if params:
        url += "?" + urlencode(sorted(params.items()))
    return url
