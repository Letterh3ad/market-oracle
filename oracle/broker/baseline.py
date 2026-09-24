"""The baseline lean: deterministic signals alone, until AI leans exist. It stays on as
the technicals-and-factors baseline the validation gate compares AI leans against."""

from __future__ import annotations

from collections.abc import Sequence

from oracle.analysts.signals import NEUTRAL_BAND, AnalystSignal
from oracle.broker.rules import Lean

SOURCE = "baseline"
INPUTS = ("technicals", "value", "quality", "growth")
STRONG = 0.6


def baseline_lean(symbol: str, signals: Sequence[AnalystSignal]) -> Lean | None:
    """None without a technicals signal: factors alone say nothing about timing."""
    used = inputs(signals)
    technicals = next((s for s in used if s.analyst == "technicals"), None)
    if technicals is None:
        return None
    weight = sum(s.confidence for s in used)
    score = sum(s.score * s.confidence for s in used) / weight if weight else 0.0
    # Missing inputs count as not agreeing, so a thin lean is a less likely one.
    agreeing = sum(s.confidence for s in used if s.score * score > 0)
    probability = 0.5 + 0.5 * agreeing / len(INPUTS)
    return Lean(symbol, technicals.as_of, _label(score), round(probability, 4), SOURCE)


def ceiling(present: int) -> float:
    """The highest probability a lean can reach with only `present` of the INPUTS."""
    return 0.5 + 0.5 * present / len(INPUTS)


def inputs(signals: Sequence[AnalystSignal]) -> list[AnalystSignal]:
    """The signals a baseline lean is built from."""
    return [s for s in signals if s.analyst in INPUTS]


def _label(score: float) -> str:
    if score <= -STRONG:
        return "strong sell"
    if score <= -NEUTRAL_BAND:
        return "sell"
    if score < NEUTRAL_BAND:
        return "hold"
    return "buy" if score < STRONG else "strong buy"
