from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

# Scores inside this band read as neutral: too weak to lean on.
NEUTRAL_BAND = 0.2


class Direction(str, Enum):
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    BULLISH = "bullish"


@dataclass(frozen=True)
class AnalystSignal:
    analyst: str
    symbol: str
    as_of: date
    """The date of the last data point used, not when it was computed."""
    score: float
    """-1 (strongly bearish) to 1 (strongly bullish)."""
    confidence: float
    """0 to 1: how much the analyst's own inputs agree."""
    reason: str
    """One plain-English sentence."""
    figures: tuple[tuple[str, float], ...]
    """The named numbers behind the signal, for the breakdown."""

    @property
    def direction(self) -> Direction:
        if self.score >= NEUTRAL_BAND:
            return Direction.BULLISH
        if self.score <= -NEUTRAL_BAND:
            return Direction.BEARISH
        return Direction.NEUTRAL


@dataclass(frozen=True)
class Skipped:
    analyst: str
    symbol: str
    reason: str
