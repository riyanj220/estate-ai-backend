"""Kernel event subscribers. The only place the kernel calls `events.subscribe`."""

from apps.kernel import events
from apps.kernel.mail import MailRequested, deliver


def ensure_subscribed() -> None:
    """Register the mail delivery handler unless present, so `events.clear()` cannot drop mail."""
    if deliver not in events._registry.get(MailRequested, ()):
        events.subscribe(MailRequested, deliver)
