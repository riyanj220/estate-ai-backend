"""Plain-text messages identity sends. Fixed copy: no account details, never logged."""

from apps.kernel import mail

SIGN_IN_PATH = "/sign-in"

_ALREADY_REGISTERED_SUBJECT = "You already have an EstateAI account"
_ALREADY_REGISTERED_BODY = """\
Someone used this email address to register on EstateAI, but an account already exists for it.

If it was you, you can sign in here:
{link}

If you have forgotten your password, use the reset option on the sign-in page.
If you did not try to register, you can ignore this message; nothing has changed.
"""


def send_already_registered(email: str) -> None:
    """Queue the notice after commit. Call inside the registration transaction."""
    mail.send(
        mail.MailMessage(
            to=email,
            subject=_ALREADY_REGISTERED_SUBJECT,
            body=_ALREADY_REGISTERED_BODY.format(link=mail.email_link(SIGN_IN_PATH)),
        )
    )


VERIFY_PATH = "/verify-email"

_VERIFY_SUBJECT = "Verify your EstateAI email address"
_VERIFY_BODY = """\
Welcome to EstateAI. Confirm your email address by opening this link:
{link}

This link works once and expires in 24 hours.
If you did not create an account, you can ignore this message.
"""


def send_verification(email: str, token: str) -> None:
    """Queue the verification link after commit. Call inside a transaction."""
    mail.send(
        mail.MailMessage(
            to=email,
            subject=_VERIFY_SUBJECT,
            body=_VERIFY_BODY.format(link=mail.email_link(VERIFY_PATH, {"token": token})),
        )
    )


RESET_PATH = "/reset-password"

_RESET_SUBJECT = "Reset your EstateAI password"
_RESET_BODY = """\
Someone asked to reset the password of your EstateAI account.
To choose a new password, open this link:
{link}

This link works once and expires in 1 hour.
If you did not ask for this, you can ignore this message.
"""


def send_password_reset(email: str, token: str) -> None:
    """Queue the reset link after commit. Call inside a transaction."""
    mail.send(
        mail.MailMessage(
            to=email,
            subject=_RESET_SUBJECT,
            body=_RESET_BODY.format(link=mail.email_link(RESET_PATH, {"token": token})),
        )
    )


_CHANGED_SUBJECT = "Your EstateAI password was changed"
_RESET_DONE_BODY = """\
The password of your EstateAI account was just reset, and all of its sessions were signed out.

If this was not you, reset your password again from the sign-in page and contact support.
"""
_CHANGED_BODY = """\
The password of your EstateAI account was just changed, and its other sessions were signed out.

If this was not you, reset your password again from the sign-in page and contact support.
"""


def send_password_reset_done(email: str) -> None:
    """Queue the notice after a reset. Fixed copy: no password, no link."""
    mail.send(mail.MailMessage(to=email, subject=_CHANGED_SUBJECT, body=_RESET_DONE_BODY))


def send_password_changed(email: str) -> None:
    """Queue the notice after a change. Fixed copy: no password, no link."""
    mail.send(mail.MailMessage(to=email, subject=_CHANGED_SUBJECT, body=_CHANGED_BODY))
