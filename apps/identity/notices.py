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
