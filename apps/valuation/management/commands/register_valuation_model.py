"""Register a trained model artifact in the database (idempotent; run after `migrate`).

python manage.py register_valuation_model [--model-version N]
"""

from django.core.management.base import BaseCommand, CommandError

from apps.kernel.actor import Actor
from apps.valuation import services


class Command(BaseCommand):
    help = "Register the newest (or the given) valuation model artifact."

    def add_arguments(self, parser):
        parser.add_argument("--model-version", type=int)

    def handle(self, *args, **options):
        try:
            row, created = services.register_model(
                Actor.system("valuation-model"), options["model_version"]
            )
        except services.RegistrationRefused as error:
            raise CommandError(str(error)) from None
        state = "registered" if created else "already registered"
        self.stdout.write(f"{row.name} version {row.version} {state}.")
