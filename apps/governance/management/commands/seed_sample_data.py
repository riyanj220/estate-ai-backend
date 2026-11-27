"""Create, remove or recreate the sample marketplace (Stories 2.16 and 11.1). Development only.

    python manage.py seed_sample_data [--reset] [--reseed] [--yes] [--password-stdin] [--listings N]

Refuses under the production settings. The password of the sample accounts (needed only when an
account is created) comes from a hidden prompt on a terminal, or one line of standard input with
`--password-stdin`; with neither it is generated and printed once, as soon as the accounts exist.
`--reset` and `--reseed` print what they will remove and need `--yes`. Output is plain
counts: never a password beyond that one line, an address or a private value. The behaviour lives
in `apps.governance.sample_data` (which builds on `apps.listings.sample_data`); this is the thin
wrapper.
"""

import getpass
import sys

from django.core.management.base import BaseCommand, CommandError

from apps.governance import sample_data as marketplace
from apps.identity.passwords import PASSWORD_MAX
from apps.kernel.errors import DomainError
from apps.listings import sample_data


class Command(BaseCommand):
    help = (
        "Create (default), --reset or --reseed the sample marketplace. "
        "Development settings only; refuses in production."
    )

    def create_parser(self, prog_name, subcommand, **kwargs):
        kwargs["allow_abbrev"] = False  # `--password` must not match `--password-stdin`
        parser = super().create_parser(prog_name, subcommand, **kwargs)
        original = parser.error

        def error(message):
            # argparse echoes the offending arguments, which could include a typed password.
            original(
                message
                if message.startswith("the following")
                else "invalid arguments (see --help); a password is never an argument"
            )

        parser.error = error
        return parser

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Remove the sample records.")
        parser.add_argument(
            "--reseed", action="store_true", help="Remove the sample records, then create them."
        )
        parser.add_argument(
            "--yes", action="store_true", help="Confirm --reset or --reseed (always required)."
        )
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Read the sample accounts' password from one line of standard input.",
        )
        parser.add_argument(
            "--listings",
            type=int,
            default=sample_data.DEFAULT_LISTINGS,
            help=f"How many Listings (1 to {sample_data.MAX_LISTINGS}, default "
            f"{sample_data.DEFAULT_LISTINGS}).",
        )

    def _password(self, from_stdin: bool) -> str | None:
        if from_stdin:
            if sys.stdin.isatty():
                raise CommandError(
                    "--password-stdin needs piped input; omit it to use the hidden prompt."
                )
            line = sys.stdin.readline(PASSWORD_MAX + 2)
            return line.rstrip("\r\n") if line.endswith(("\n", "\r")) else line
        if not sys.stdin.isatty():
            return None  # generated
        try:
            first = getpass.getpass("Password for the sample accounts (empty: generate one): ")
            if not first:
                return None
            if getpass.getpass("Password (again): ") != first:
                raise CommandError("The passwords do not match.")
        except EOFError:
            return None
        return first

    def _announce(self, password: str) -> None:
        self.stdout.write(f"seed accounts password: {password}")
        self.stdout.flush()

    def handle(self, *args, **options):
        reset, reseed = options["reset"], options["reseed"]
        if reset and reseed:
            raise CommandError("Use --reset or --reseed, not both.")
        if options["password_stdin"] and reset:
            raise CommandError("--password-stdin does not apply to --reset.")
        if options["yes"] and not (reset or reseed):
            raise CommandError("--yes applies to --reset and --reseed only.")
        given = options["password_stdin"]
        try:
            sample_data.require_allowed()
            if reset or reseed:
                will = marketplace.preview_reset()
                self.stdout.write(
                    f"will remove: {will.accounts_removed} accounts, "
                    f"{will.listings_removed} listings, {will.events_removed} publication events"
                )
                self.stdout.write(
                    "will also remove: "
                    + ", ".join(f"{count} {label}" for label, count in will.activity.items())
                )
                if not options["yes"]:
                    raise CommandError("Refused: --reset and --reseed need --yes.")
            if reset:
                report = marketplace.reset()
            else:
                needs = reseed or sample_data.accounts_missing() > 0
                password = self._password(options["password_stdin"]) if needs else None
                given = given or password is not None
                run = marketplace.reseed if reseed else marketplace.seed
                try:
                    report = run(options["listings"], password, self._announce)
                except Exception:
                    if sample_data.accounts_missing() < len(sample_data.ACCOUNTS):
                        self.stdout.write("The run failed: run with --reseed to start over.")
                    raise
        except sample_data.SeedRefused as exc:
            raise CommandError(str(exc)) from None
        except DomainError:
            raise CommandError("The request was refused.") from None
        for label, value in (
            ("accounts created", report.accounts_created),
            ("listings created", report.listings_created),
            ("images created", report.images_created),
            ("floor plans created", report.floor_plans_created),
            ("accounts removed", report.accounts_removed),
            ("listings removed", report.listings_removed),
            ("publication events removed", report.events_removed),
        ):
            self.stdout.write(f"{label}: {value}")
        for label, value in report.activity.items():
            self.stdout.write(f"{label} {'deleted' if reset else 'created'}: {value}")
        if given and not reset and report.accounts_existing:
            self.stdout.write("notice: existing sample accounts keep their password; use --reseed.")
