from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.kernel import retention


class Command(BaseCommand):
    help = "Run every registered retention job (PRD section 8.3). Schedule it at deployment."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would be deleted; delete nothing."
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        results = retention.run_all(timezone.now(), dry_run)
        if not results:
            self.stdout.write("no retention jobs registered")
        verb = "would delete" if dry_run else "deleted"
        for result in results:
            if result.error_class:
                self.stdout.write(f"{result.name}: FAILED ({result.error_class})")
            else:
                self.stdout.write(f"{result.name}: {verb} {result.count}")
        failed = sum(1 for r in results if r.error_class)
        if failed:
            raise CommandError(f"{failed} retention job(s) failed")
