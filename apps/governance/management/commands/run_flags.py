from django.core.management.base import BaseCommand

from apps.governance import flags
from apps.kernel.actor import Actor


class Command(BaseCommand):
    help = (
        "Run the duplicate and suspicious Listing flag rules (Story 9.6). Schedule it nightly at "
        "deployment. Flags are advisory: no Listing changes."
    )

    def handle(self, *args, **options):
        added = flags.run_flags(Actor.system("run_flags"))
        for kind, count in added.items():
            self.stdout.write(f"{kind}: {count} new")
