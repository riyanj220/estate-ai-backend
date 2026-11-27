"""The recorded-stub adapter: the default, and the only one tests use.

It implements all three ports without any network and records every request it receives, so tests
can prove what would have left the machine. Tests script it with `reply`, `fail_with` and `delay`.
"""

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field

from apps.kernel.ai.ports import Audio, LLMRequest, LLMResponse, require_redacted

HISTORY = 1000  # a long-running development server must not grow without bound
EMBEDDING_DIMENSIONS = 8


@dataclass(frozen=True)
class Recorded:
    port: str
    texts: tuple[str, ...] = field(repr=False, default=())
    schema: dict | None = field(repr=False, default=None)
    audio: bytes = field(repr=False, default=b"")


class RecordedStub:
    provider = "stub"
    model = "recorded-stub"
    local_embeddings = True

    def __init__(self) -> None:
        self.requests: deque[Recorded] = deque(maxlen=HISTORY)
        self.reset()

    def reset(self) -> None:
        self.requests.clear()
        self.reply = "This is a stub reply."
        self.fail_with: Exception | None = None
        self.delay = 0.0

    def _record(self, recorded: Recorded) -> None:
        self.requests.append(recorded)
        if self.delay:
            time.sleep(self.delay)
        if self.fail_with is not None:
            raise self.fail_with

    def complete(self, request: LLMRequest, *, timeout: float) -> LLMResponse:
        require_redacted(*request.texts())
        self._record(Recorded("llm", tuple(t.value for t in request.texts()), request.schema))
        return LLMResponse(self.reply)

    def embed(self, texts, *, timeout: float) -> list[list[float]]:
        require_redacted(*texts)
        self._record(Recorded("embeddings", tuple(t.value for t in texts)))
        return [
            [
                byte / 255
                for byte in hashlib.sha256(t.value.encode()).digest()[:EMBEDDING_DIMENSIONS]
            ]
            for t in texts
        ]

    def transcribe(self, audio: Audio, *, timeout: float) -> str:
        self._record(Recorded("speech", audio=audio.data))
        return self.reply


stub = RecordedStub()
