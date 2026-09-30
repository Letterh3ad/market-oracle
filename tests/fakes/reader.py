"""A stand-in for the stronger reader: FakeLaya's reading as codes, and headlines in `unparsed`
left without an answer. Records every batch and whether it was closed."""
from __future__ import annotations

from oracle.research.laya import LayaInput, LayaScore
from oracle.research.reader import ReaderStats, reader_tag
from tests.fakes.laya import FakeLaya


class FakeReader(FakeLaya):
    def __init__(self, model: str = "fake", unparsed: set[str] | frozenset[str] = frozenset()) -> None:
        super().__init__(reader_tag(model), "ollama")
        self.unparsed = set(unparsed)
        self.stats = ReaderStats()

    def score(self, items: list[LayaInput]) -> list[LayaScore | None]:
        out = [None if i.headline in self.unparsed else s for i, s in zip(items, super().score(items))]
        self.stats.calls += 1
        self.stats.items += len(items)
        self.stats.unparsed += sum(s is None for s in out)
        self.stats.seconds += 0.01 * len(items)
        return out
