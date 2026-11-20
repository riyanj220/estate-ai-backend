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


CONFIRM_EMAIL_CHANGE_PATH = "/confirm-email-change"

_CHANGE_CONFIRM_SUBJECT = "Confirm your new EstateAI sign-in email"
_CHANGE_CONFIRM_BODY = """\
Someone asked to use this email address to sign in to an EstateAI account.
To confirm the change, open this link:
{link}

This link works once and expires in 24 hours.
If you did not ask for this, you can ignore this message; nothing has changed.
"""


def send_email_change_confirmation(new_email: str, token: str) -> None:
    """Queue the confirmation link to the NEW address after commit. Call inside a transaction."""
    mail.send(
        mail.MailMessage(
            to=new_email,
            subject=_CHANGE_CONFIRM_SUBJECT,
            body=_CHANGE_CONFIRM_BODY.format(
                link=mail.email_link(CONFIRM_EMAIL_CHANGE_PATH, {"token": token})
            ),
        )
    )


_CHANGE_REQUESTED_SUBJECT = "A change of your EstateAI sign-in email was requested"
_CHANGE_REQUESTED_BODY = """\
A change of the sign-in email of your EstateAI account was requested.
Nothing changes until the new address confirms it, and you can keep signing in with this address.

If this was not you, change your password now.
"""


def send_email_change_requested(current_email: str) -> None:
    """Queue the heads-up to the CURRENT address. Fixed copy: no new address, no link."""
    mail.send(
        mail.MailMessage(
            to=current_email,
            subject=_CHANGE_REQUESTED_SUBJECT,
            body=_CHANGE_REQUESTED_BODY,
        )
    )


_CHANGED_EMAIL_SUBJECT = "Your EstateAI sign-in email was changed"
_CHANGED_EMAIL_BODY = """\
The sign-in email of your EstateAI account was changed to a new address.
This address no longer signs in to that account.

If this was not you, contact support.
"""


def send_email_changed(old_email: str) -> None:
    """Queue the notice to the OLD address after a change. Fixed copy: no new address."""
    mail.send(
        mail.MailMessage(to=old_email, subject=_CHANGED_EMAIL_SUBJECT, body=_CHANGED_EMAIL_BODY)
    )
