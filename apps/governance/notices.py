"""Emails of the closure decisions (FR-6). The reason is the administrator's, checked to hold no
contact details; nothing else about the account is in them."""

from apps.kernel import mail

_APPROVED_SUBJECT = "Your EstateAI account has been closed"
_APPROVED_BODY = """\
Your request to close your EstateAI account was approved. You can no longer sign in, your
personal details have been removed and your listings have been archived.

Reason given: {reason}
"""

_REJECTED_SUBJECT = "Your EstateAI account closure request was not approved"
_REJECTED_BODY = """\
Your request to close your EstateAI account was not approved, and nothing has changed.

Reason given: {reason}
"""


def send_closure_approved(email: str, reason: str) -> None:
    """Queue after commit. Call inside the decision's transaction."""
    mail.send(
        mail.MailMessage(
            to=email, subject=_APPROVED_SUBJECT, body=_APPROVED_BODY.format(reason=reason)
        )
    )


def send_closure_rejected(email: str, reason: str) -> None:
    """Queue after commit. Call inside the decision's transaction."""
    mail.send(
        mail.MailMessage(
            to=email, subject=_REJECTED_SUBJECT, body=_REJECTED_BODY.format(reason=reason)
        )
    )
