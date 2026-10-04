"""A stand-in for the Ollama embedding model: each concept is one axis, set when the text holds any of
its phrases, so texts sharing a concept but no words point the same way; each word adds a little on a
hashed axis of its own, so texts without a concept differ."""

import re
import zlib

from oracle.research.llm import ModelError

YEARLY_HIGH = ("52-week high", "yearly high", "annual peak")
TREND = ("10-month ma", "200-day average", "moving average")
WORD_AXES = 512
WORD_WEIGHT = 0.1
DOWN = "Ollama is not reachable at http://localhost:11434 (refused): start it with `ollama serve`"


class FakeEmbed:
    def __init__(self, *concepts: tuple[str, ...], model: str = "fake-embed", down: bool = False) -> None:
        self.concepts = concepts or (YEARLY_HIGH, TREND)
        self.model = model
        self.down = down
        self.calls: list[tuple[list[str], bool]] = []

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]:
        self.calls.append((list(texts), query))
        if self.down:
            raise ModelError(DOWN)
        return [self._vector(t.lower()) for t in texts]

    def _vector(self, text: str) -> list[float]:
        words = [0.0] * WORD_AXES
        for w in re.findall(r"[a-z0-9]{3,}", text):
            words[zlib.crc32(w.encode()) % WORD_AXES] += WORD_WEIGHT
        return [float(any(p in text for p in phrases)) for phrases in self.concepts] + words
