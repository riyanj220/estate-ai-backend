from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.utils import timezone

from apps.kernel import audit, retention


class Command(BaseCommand):
    help = (
        "Operator command: delete audit records older than 24 months with the purge database "
        "role (PURGE_PASSWORD). Not part of run_retention."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Report the count; delete nothing."
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        try:
            count = audit.purge_expired(timezone.now(), dry_run)
        except retention.PurgeRoleNotConfigured as exc:
            raise CommandError(f"Nothing deleted: {exc}.") from None
        except DatabaseError as exc:
            # The class only: a database message can name the host or the role.
            raise CommandError(f"Nothing deleted: {type(exc).__name__}.") from None
        verb = "would delete" if dry_run else "deleted"
        self.stdout.write(f"audit records older than 24 months: {verb} {count}")
