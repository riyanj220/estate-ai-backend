from pathlib import Path

from django.core.management.base import BaseCommand

from apps.presentation.realtime import protocol


class Command(BaseCommand):
    help = "Write the WebSocket event schema (events.schema.json)."
    requires_system_checks: list = []

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)

    def handle(self, *args, **options):
        Path(options["file"]).write_text(protocol.schema_text(), encoding="utf-8")
