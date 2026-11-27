"""The recorded-stub adapter: the default, and the only one tests use.

It implements all three ports without any network and records every request it receives, so tests
can prove what would have left the machine. Tests script it with `reply`, `fail_with` and `delay`.

Its embeddings are hashed bags of words: texts that share words get similar vectors, so knowledge
retrieval works offline. They carry no meaning beyond that.
"""

import hashlib
import math
import re
import time
from collections import deque
from dataclasses import dataclass, field

from apps.kernel.ai.ports import Audio, LLMRequest, LLMResponse, require_redacted

HISTORY = 1000  # a long-running development server must not grow without bound
EMBEDDING_DIMENSIONS = 768
_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from how i if in is it its me my no not of on "
    "or so that the their them there this to was what when where which who why will with you "
    "your".split()
)


def bag_of_words_vector(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in _STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        digest = hashlib.sha256(word.encode()).digest()
        vector[int.from_bytes(digest[:4]) % EMBEDDING_DIMENSIONS] += 1.0
    length = math.sqrt(sum(v * v for v in vector))
    return [v / length for v in vector] if length else vector


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
        return [bag_of_words_vector(t.value) for t in texts]

    def transcribe(self, audio: Audio, *, timeout: float) -> str:
        self._record(Recorded("speech", audio=audio.data))
        return self.reply


stub = RecordedStub()
