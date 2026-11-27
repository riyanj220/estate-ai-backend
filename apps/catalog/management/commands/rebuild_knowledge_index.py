"""Rebuild the knowledge base index from the authored passages (Story 5.5)."""

from django.core.management.base import BaseCommand, CommandError

from apps.catalog import services
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway


class Command(BaseCommand):
    help = "Embed the knowledge base with the configured AI_PROVIDER and replace the index."

    def add_arguments(self, parser):
        parser.add_argument(
            "--pause", type=float, default=0.0, help="Seconds between embedding requests."
        )

    def handle(self, *args, **options):
        model = gateway.embedding_model_key()
        try:
            count = services.rebuild_knowledge_index(Actor.system(), pause=options["pause"])
        except services.KnowledgeIndexError as error:
            hint = (
                ". This looks like a rate limit (HTTP 429): wait, then retry with --pause, "
                "for example --pause 60."
                if error.status == 429
                else ""
            )
            raise CommandError(f"Index unchanged ({model}): {error}{hint}") from None
        self.stdout.write(f"Indexed {count} passages with {model}.")
