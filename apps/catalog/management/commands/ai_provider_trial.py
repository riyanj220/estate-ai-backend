"""The provider availability trial (Story 5.4): real Gemini calls with synthetic text only.

It prints model names, latencies, success counts and error categories, never a key, a header or a
provider body. It calls the adapter directly (no database), whatever `AI_PROVIDER` says, and is
never run by tests. Results are recorded by hand in `docs/ai-providers.md`.
"""

import json
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.extraction import extraction_schema
from apps.kernel.ai.gemini import gemini
from apps.kernel.ai.ports import Audio, LLMRequest
from apps.kernel.ai.redaction import KnownValues, redact
from apps.kernel.ai.transport import ProviderError

TIMEOUT = 30.0
SYSTEM = (
    "Extract property facts from the user's text. Use null for anything not stated. "
    "Never guess. Money and measurements are plain digit strings."
)
# Invented sentences: (purpose, subtype, text, facts that must come back).
SAMPLES = (
    (
        "sale",
        "house",
        "Selling a 240 square yard house with 4 bedrooms and 5 bathrooms for 45000000 rupees.",
        {"bedrooms": 4, "bathrooms": 5, "area_unit": "sq_yd"},
    ),
    (
        "rent",
        "apartment",
        "Flat for rent on floor 3, 2 bedrooms, there is a lift, pets are not allowed.",
        {"bedrooms": 2, "floor_number": 3, "lift": True, "pets_allowed": False},
    ),
    (
        "sale",
        "residential_plot",
        "Corner plot for sale, park facing, 30 by 60 feet, gas is not available yet.",
        {"corner_plot": True, "park_facing": True, "gas": False},
    ),
    (
        "rent",
        "shop",
        "Dukaan kiraye ke liye, ground floor, bijli aur pani mojood hai, 15 feet front.",
        {"floor_number": 0, "electricity": True, "water": True},
    ),
)


def _text(value: str):
    return redact(value, KnownValues())


def conforms(reply: str, schema: dict) -> dict | None:
    """The parsed reply when it is an object with exactly the schema's keys and enum values."""
    try:
        data = json.loads(reply)
    except ValueError:
        return None
    if not isinstance(data, dict) or set(data) != set(schema["required"]):
        return None
    for name, rule in schema["properties"].items():
        value = data[name]
        if value is None:
            continue
        if "anyOf" in rule:
            if value not in rule["anyOf"][0]["enum"]:
                return None
        elif type(value) is not {"string": str, "integer": int, "boolean": bool}[rule["type"][0]]:
            return None
    return data


class Command(BaseCommand):
    help = "Call the real AI provider with synthetic text and report availability."

    def add_arguments(self, parser):
        parser.add_argument("--audio", help="a synthetic WAV file for the speech sample")
        parser.add_argument("--samples", type=int, default=len(SAMPLES))
        parser.add_argument("--skip-list", action="store_true")

    def _try(self, label: str, fn):
        started = time.monotonic()
        try:
            value = fn()
        except ProviderError as error:
            self.stdout.write(f"{label}: FAILED {error.category} status={error.status}")
            if error.category in ("auth", "quota", "not_configured"):
                raise CommandError("Stopped: keep AI_PROVIDER=stub.") from None
            return None
        self.stdout.write(f"{label}: ok {round((time.monotonic() - started) * 1000)} ms")
        return value

    def handle(self, *args, **options):
        self.stdout.write(f"llm={gemini.model} embeddings={gemini.embedding_model}")
        if not options["skip_list"]:
            for item in self._try("list_models", lambda: gemini.list_models(timeout=TIMEOUT)) or []:
                self.stdout.write(
                    f"  {item.get('name')} in={item.get('inputTokenLimit')} "
                    f"out={item.get('outputTokenLimit')} "
                    f"{','.join(item.get('supportedGenerationMethods', []))}"
                )
        passed = 0
        samples = SAMPLES[: max(options["samples"], 0)]
        for number, (purpose, subtype, text, expected) in enumerate(samples, 1):
            schema = extraction_schema(purpose, subtype)
            request = LLMRequest(system=_text(SYSTEM), input=_text(text), schema=schema)
            label = f"extract_{number} {subtype} schema_bytes={len(json.dumps(schema))}"
            reply = self._try(label, lambda r=request: gemini.complete(r, timeout=TIMEOUT))
            if reply is None:
                continue
            data = conforms(reply.text, schema)
            if data is None:
                self.stdout.write("  schema: not conforming")
                continue
            passed += 1
            right = sum(data.get(name) == value for name, value in expected.items())
            self.stdout.write(f"  schema: conforming; expected facts {right}/{len(expected)}")
        self.stdout.write(f"structured_output: {passed}/{len(samples)}")
        vectors = self._try(
            "embeddings",
            lambda: gemini.embed(
                (_text("How is stamp duty paid?"), _text("A two bedroom flat.")), timeout=TIMEOUT
            ),
        )
        if vectors:
            self.stdout.write(f"  vectors={len(vectors)} dimensions={len(vectors[0])}")
        if options["audio"]:
            audio = Audio(Path(options["audio"]).read_bytes(), "audio/wav")
            heard = self._try("speech", lambda: gemini.transcribe(audio, timeout=TIMEOUT))
            if heard:
                self.stdout.write(f"  transcript (synthetic audio): {heard[:200]!r}")
