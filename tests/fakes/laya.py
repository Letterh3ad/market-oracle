"""A stand-in for the Laya model: headlines naming a rise score up, a fall down, and a
headline naming the company is relevant. Records what it was asked, and whether it was closed."""
from __future__ import annotations

from oracle.research.laya import DIRECTIONS, LayaInput, LayaScore


class FakeLaya:
    def __init__(self, tag: str = "fake-laya", device: str = "cpu") -> None:
        self.tag, self.device = tag, device
        self.batches: list[list[LayaInput]] = []
        self.closed = False

    def score(self, items: list[LayaInput]) -> list[LayaScore]:
        self.batches.append(list(items))
        return [self._one(i) for i in items]

    def close(self) -> None:
        self.closed = True

    @staticmethod
    def _one(item: LayaInput) -> LayaScore:
        text = item.headline.lower()
        direction = "up" if "rise" in text or "beat" in text else "down" if "fall" in text or "recall" in text else "none"
        probs = {d: (0.8 if d == direction else 0.1) for d in DIRECTIONS}
        name = item.company.split(" (")[0].split()[0].lower()
        event = "recall_safety" if "recall" in text else "earnings" if "beat" in text else "other"
        return LayaScore(0.9 if name in text else 0.2, direction, probs, event, 0.7)
