"""Measure transcription on recorded queries (Story 6.3): word error rate and p90 latency.

The directory holds clips (`.webm`, `.ogg`, `.mp4`, `.m4a`, `.wav`), each with a `.txt` of the same
name holding the words spoken. The part of the name before the first `-` groups the report, for
example `en-house.webm`, `ur-house.webm`, `romanurdu-house.webm`. Clips are not kept in the
repository. One AI call per clip, from the open part of the daily budget.
"""

import math
import time
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.kernel import ratelimit
from apps.kernel.actor import Actor
from apps.kernel.ai import gateway
from apps.kernel.ai.adapters import get_adapter
from apps.kernel.ai.ports import Audio
from apps.search import audio, wer

SUFFIXES = (".webm", ".ogg", ".mp4", ".m4a", ".wav")


class Command(BaseCommand):
    help = "Word error rate and p90 latency of voice search transcription."

    def add_arguments(self, parser):
        parser.add_argument("directory")
        parser.add_argument("--pause", type=float, default=4.0, help="seconds between calls")

    def handle(self, *args, directory, pause=4.0, **options):
        clips = sorted(p for p in Path(directory).glob("*") if p.suffix in SUFFIXES)
        clips = [p for p in clips if p.with_suffix(".txt").is_file()]
        if not clips:
            raise CommandError("No clips with a .txt beside them.")
        totals: dict[str, list[int]] = {}
        latencies = []
        for number, clip in enumerate(clips):
            data = clip.read_bytes()
            kind = audio.content_type(data)
            if kind is None:
                self.stdout.write(f"not an accepted recording: {clip.name}")
                continue
            if number:
                time.sleep(pause)
            started = time.monotonic()
            result = gateway.transcribe(
                Actor.system(),
                Audio(data, kind),
                visitor_key=ratelimit.derive_key("evaluate_speech", str(uuid.uuid4())),
            )
            if not result.ok:
                self.stdout.write(f"{result.outcome}: {clip.name}")
                continue
            latencies.append(time.monotonic() - started)
            wrong, said = wer.errors(clip.with_suffix(".txt").read_text("utf-8"), result.value)
            total = totals.setdefault(clip.stem.split("-")[0], [0, 0, 0])
            total[0] += wrong
            total[1] += said
            total[2] += 1
        model = f"{get_adapter().provider}:{get_adapter().model}"
        self.stdout.write(f"{model}: {len(latencies)} of {len(clips)} clips transcribed")
        for group, (wrong, said, count) in sorted(totals.items()):
            share = wrong / said if said else 0
            self.stdout.write(f"{group}: word error rate {share:.1%} over {count} clips")
        if latencies:
            p90 = sorted(latencies)[math.ceil(0.9 * len(latencies)) - 1]
            self.stdout.write(f"p90 latency: {p90:.1f}s")
