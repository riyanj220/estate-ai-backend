"""Add price drivers to estimates stored before drivers existed. Safe to run again."""

from django.core.management.base import BaseCommand

from apps.kernel.actor import Actor
from apps.valuation import services


class Command(BaseCommand):
    help = "Fill in what moves each stored price estimate, where it is missing."

    def handle(self, *args, **options):
        filled = services.explain_stored_estimates(Actor.system("valuation"))
        self.stdout.write(f"estimates explained: {filled}")
