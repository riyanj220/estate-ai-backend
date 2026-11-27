"""The only way an administrator account comes into existence or is recovered (Story 1.22).

    python manage.py provision_admin <action> --email <address>

where <action> is create, disable, enable, remove, reset-password or reset-second-factor.

The password for `create` and `reset-password` is read from a hidden prompt (asked twice) or, for
automation, one line of standard input with `--password-stdin`. It is never an argument, never
defaulted, never printed or logged. Every refusal is one line and a non-zero exit. The command
only calls `identity.services` as the system actor.
"""

import getpass
import sys

from django.core.management.base import BaseCommand, CommandError

from apps.identity import services
from apps.identity.passwords import PASSWORD_MAX
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError, field_message

ACTIONS = ("create", "disable", "enable", "remove", "reset-password", "reset-second-factor")
_FORCEABLE = {"disable", "remove"}
_NEEDS_PASSWORD = {"create", "reset-password"}
_ARGUMENT_ERROR = "invalid arguments (see --help); a password is never accepted on the command line"
_REFUSALS = {
    "invalid_email": "The email address is not valid.",
    "email_taken": "An account with that address already exists.",
    "not_an_administrator": "That address does not belong to an administrator.",
    "last_administrator": (
        "That is the last active administrator; add --force-last to proceed anyway."
    ),
}


class Command(BaseCommand):
    help = (
        "Provision administrators: create, disable, remove, reset-password or reset-second-factor. "
        "Passwords come from a prompt or --password-stdin, never from an argument."
    )

    def create_parser(self, prog_name, subcommand, **kwargs):
        kwargs["allow_abbrev"] = False  # `--password` must not match `--password-stdin`
        parser = super().create_parser(prog_name, subcommand, **kwargs)
        original = parser.error

        def error(message):
            # argparse echoes the offending arguments, which could include a typed password.
            original(message if message.startswith("the following") else _ARGUMENT_ERROR)

        parser.error = error
        return parser

    def add_arguments(self, parser):
        parser.add_argument("action", choices=ACTIONS)
        parser.add_argument("--email", required=True, help="The administrator's email address.")
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Read the password from one line of standard input instead of prompting.",
        )
        parser.add_argument(
            "--force-last",
            action="store_true",
            help="disable and remove only: allow it for the last active administrator.",
        )

    def _password(self, from_stdin: bool) -> str:
        if from_stdin:
            if sys.stdin.isatty():
                raise CommandError(
                    "--password-stdin needs piped input; omit it to use the hidden prompt."
                )
            # One line, never more than the longest acceptable password plus its line ending.
            line = sys.stdin.readline(PASSWORD_MAX + 2)
            return line.rstrip("\r\n") if line.endswith(("\n", "\r")) else line
        try:
            first = getpass.getpass("Password: ")
            again = getpass.getpass("Password (again): ")
        except EOFError:
            raise CommandError("No password was provided.") from None
        if again != first:
            raise CommandError("The passwords do not match.")
        return first

    def handle(self, *args, **options):
        action, email = options["action"], options["email"]
        if options["password_stdin"] and action not in _NEEDS_PASSWORD:
            raise CommandError("--password-stdin applies to create and reset-password only.")
        if options["force_last"] and action not in _FORCEABLE:
            raise CommandError("--force-last applies to disable and remove only.")
        system = Actor.system("provision_admin")
        try:
            if action == "create":
                services.provision_admin(system, email, self._password(options["password_stdin"]))
                self.stdout.write("Administrator created.")
            elif action == "disable":
                changed = services.disable_admin(system, email, force_last=options["force_last"])
                self.stdout.write(
                    "Administrator disabled." if changed else "Administrator was already disabled."
                )
            elif action == "enable":
                changed = services.enable_admin(system, email)
                self.stdout.write(
                    "Administrator enabled (earlier sessions stay ended)."
                    if changed
                    else "Administrator was already enabled."
                )
            elif action == "remove":
                services.remove_admin(system, email, force_last=options["force_last"])
                self.stdout.write("Administrator removed.")
            elif action == "reset-second-factor":
                changed = services.reset_second_factor(system, email)
                self.stdout.write(
                    "Second factor removed; every session of that administrator ended."
                    if changed
                    else "That administrator has no second factor."
                )
            else:
                services.reset_admin_password(
                    system, email, self._password(options["password_stdin"])
                )
                self.stdout.write("Password reset; every session of that administrator ended.")
        except services.PasswordInvalid as exc:
            raise CommandError(field_message(exc.fields["password"])) from None
        except services.AdminRefused as exc:
            raise CommandError(_REFUSALS[exc.reason]) from None
        except (DomainError, ValueError):
            raise CommandError("The request was refused.") from None
