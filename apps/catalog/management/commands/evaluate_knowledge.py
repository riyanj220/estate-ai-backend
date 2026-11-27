"""Measure retrieval against the labelled questions (SM-11, Story 5.7). One embedding request."""

import yaml
from django.core.management.base import BaseCommand, CommandError

from apps.catalog import knowledge, selectors
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway
from apps.kernel.ai.redaction import KnownValues, redact

TARGET = 32  # of 40


class Command(BaseCommand):
    help = "Top-3 accuracy and similarity scores on the labelled questions, with the current index."

    def handle(self, *args, **options):
        data = yaml.safe_load(knowledge.QUESTIONS.read_text(encoding="utf-8"))
        labelled, off_topic = data["questions"], data["off_topic"]
        asked = [item["question"] for item in labelled] + off_topic
        model = gateway.embedding_model_key()
        result = gateway.embed(Actor.system(), [redact(q, KnownValues()) for q in asked])
        if not result.ok:
            raise CommandError(f"Embedding failed: {result.outcome}")
        ranked = [selectors.rank_knowledge(vector, model) for vector in result.value]
        if not all(ranked):
            raise CommandError(f"No index for {model}: run rebuild_knowledge_index first.")
        hits, right_scores = 0, []
        for item, top in zip(labelled, ranked, strict=False):
            found = {key: score for score, key in top}
            if item["passage"] in found:
                hits += 1
                right_scores.append(found[item["passage"]])
            else:
                self.stdout.write(f"miss: {item['question']} -> {[key for _, key in top]}")
        threshold = selectors.KNOWLEDGE_MIN_SCORE[model.split(":", 1)[0]]
        noise = [top[0][0] for top in ranked[len(labelled) :]]
        self.stdout.write(f"{model}: {hits}/{len(labelled)} in the top 3 (target {TARGET})")
        self.stdout.write(
            f"threshold {threshold}; lowest correct score {min(right_scores, default=0):.3f} "
            f"({sum(s < threshold for s in right_scores)} below it); "
            f"highest off-topic score {max(noise, default=0):.3f} "
            f"({sum(s >= threshold for s in noise)} at or above it)"
        )
