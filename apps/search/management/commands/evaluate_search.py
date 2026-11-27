"""Measure the configured model on the labelled queries (SM-3, Story 6.1): one AI call per query.

It spends the open part of the daily AI budget, so run it on purpose.
"""

import math
import time
import uuid

from django.core.management.base import BaseCommand

from apps.kernel import ratelimit
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway
from apps.kernel.ai.adapters import get_adapter
from apps.search import interpretation, labelled, services


class Command(BaseCommand):
    help = "Accuracy, rejected and repaired shares and p90 latency of natural-language search."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, help="only the first N queries")
        parser.add_argument("--pause", type=float, default=4.0, help="seconds between calls")

    def handle(self, *args, limit=None, pause=4.0, **options):
        items = labelled.load()[:limit]
        actor = Actor.system()
        matched, latencies, qualities, failed = 0, [], [], 0
        for number, item in enumerate(items):
            if number:
                time.sleep(pause)
            request = services.request_for(actor, item["query"])
            started = time.monotonic()
            result = gateway.complete(
                actor,
                gateway.SEARCH_QUERY,
                request,
                visitor_key=ratelimit.derive_key("evaluate_search", str(uuid.uuid4())),
            )
            if not result.ok:
                failed += 1
                self.stdout.write(f"{result.outcome}: {item['query']}")
                continue
            latencies.append(time.monotonic() - started)
            read = interpretation.interpret(result.value.text, request.input.value)
            qualities.append(read.quality)
            if labelled.named(read.params) == item["params"]:
                matched += 1
            else:
                self.stdout.write(f"differs: {item['query']} -> {labelled.named(read.params)}")
        answered = len(qualities)
        model = f"{get_adapter().provider}:{get_adapter().model}"
        self.stdout.write(f"{model}: {answered} answered, {failed} failed of {len(items)}")
        if not answered:
            return
        self.stdout.write(f"expected filters: {matched}/{answered}")
        for quality in (interpretation.REJECTED, interpretation.REPAIRED):
            share = qualities.count(quality) / answered
            self.stdout.write(f"{quality}: {share:.1%}")
        p90 = sorted(latencies)[math.ceil(0.9 * answered) - 1]
        self.stdout.write(f"p90 latency: {p90:.1f}s")
