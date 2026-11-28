"""SMTP delivery to a real provider (hosted settings only) that never mails a reserved address.

Sample accounts live on a `.invalid` domain: handing such a message to a real provider would only
produce bounces that count against the sender. Reserved names (RFC 2606 and 6761) are dropped from
the recipients before anything reaches the network; a message with none left is not sent.
"""

import logging
from email.utils import parseaddr

from django.core.mail.backends.smtp import EmailBackend

logger = logging.getLogger("estate.mail")

_RESERVED_SUFFIXES = (".invalid", ".test", ".example", ".localhost")
_RESERVED_DOMAINS = ("localhost", "example.com", "example.net", "example.org")


def is_undeliverable(address: str) -> bool:
    domain = parseaddr(address)[1].rpartition("@")[2].strip().rstrip(".").lower()
    if not domain:
        return True
    if domain.endswith(_RESERVED_SUFFIXES):
        return True
    return any(domain == name or domain.endswith("." + name) for name in _RESERVED_DOMAINS)


class ReservedSafeSMTPBackend(EmailBackend):
    def send_messages(self, email_messages):
        deliverable = []
        for message in email_messages or ():
            for field in ("to", "cc", "bcc"):
                setattr(
                    message, field, [a for a in getattr(message, field) if not is_undeliverable(a)]
                )
            if message.recipients():
                deliverable.append(message)
            else:
                logger.info(
                    "mail to a reserved address not sent", extra={"event": "mail.skipped_reserved"}
                )
        return super().send_messages(deliverable) if deliverable else 0
