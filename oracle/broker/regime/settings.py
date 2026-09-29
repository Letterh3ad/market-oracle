"""The regime lines' vocabulary and knobs: the methods blended, the moods read, the preset
weights (priors from the literature, overridable in `[regime]`) and the non-price inputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

METHODS = ("trend", "momentum", "reversion", "low_vol", "quality_value", "earnings_drift", "hold", "ai_verdict")
MOODS = ("calm_up", "volatile_up", "calm_down", "volatile_down")
FLAGS = ("clustered", "rebound", "choppy")

PRESETS: Mapping[str, Mapping[str, float]] = {
    mood: dict(zip(METHODS, row)) for mood, row in {
        "calm_up": (.15, .25, .05, .05, .10, .10, .15, .15),
        "volatile_up": (.10, .10, .15, .20, .10, .10, .10, .15),
        "calm_down": (.25, .10, .10, .20, .15, .05, .00, .15),
        "volatile_down": (.20, .00, .15, .25, .15, .05, .05, .15),
    }.items()}
"""Trend and low volatility carry falling markets, momentum rising calm ones (it crashes in
volatile rebounds, Daniel-Moskowitz 2016), reversion volatile ones (Connors)."""

LABEL_SCORES: Mapping[str, float] = {"strong sell": -1.0, "sell": -0.5, "hold": 0.0, "buy": 0.5, "strong buy": 1.0}
"""A research verdict's label as the `ai_verdict` method's score."""

DatedScores = Sequence[tuple[date, float]]
"""(day the score was known, score in [-1, 1]), oldest first."""


@dataclass(frozen=True)
class RegimeSettings:
    weights: Mapping[str, Mapping[str, float]] = field(default_factory=lambda: PRESETS)
    """mood -> method -> preset weight; each mood's row is renormalised."""
    span: int = 5
    """Trading days the final weights are smoothed over (exponential), so one noisy day never flips the book."""
    sector_share: float = 0.3
    """At most ceil(this x max_positions) buys plus holdings per sector."""
    max_positions: int = 10
    min_probability: float = 0.6
    """The rules' buy threshold: every buy lean clears it, so the label alone decides a buy."""
    warning_factor: float = 0.5
    good_factor: float = 1.25
    playbook_cap: float = 1.5
    tilt_cap: float = 0.5
    """The AI tilt multiplies each method's weight within [1 - cap, 1 + cap]."""


@dataclass(frozen=True)
class RegimeInputs:
    """The non-price inputs, each optional: a method without its input gives no score."""

    sectors: Mapping[str, str] = field(default_factory=dict)
    """symbol -> sector, for the sector cap; a listing without one is not capped."""
    earnings: Mapping[str, Sequence[tuple[date, bool | None]]] = field(default_factory=dict)
    """symbol -> (release day, before the open?) per results release (SEC 8-K item 2.02)."""
    factors: Mapping[str, DatedScores] = field(default_factory=dict)
    """symbol -> the stored value and quality signals' combined score, dated."""
    verdicts: Mapping[str, DatedScores] = field(default_factory=dict)
    """symbol -> research verdicts as scores (`LABEL_SCORES`), dated."""
    verdict_max_age: int = 7
    """Calendar days a verdict stays usable, the AI account's freshness rule."""


def settings_from(values: Mapping[str, object], *, max_positions: int, min_probability: float) -> RegimeSettings:
    """From the flat `[regime]` config (`<mood>_<method>` weight keys beside the others)."""
    weights = {m: {k: float(values.get(f"{m}_{k}", PRESETS[m][k])) for k in METHODS} for m in MOODS}
    default = RegimeSettings()
    return RegimeSettings(
        weights=weights, span=int(values.get("smoothing_span", default.span)),
        sector_share=float(values.get("sector_share", default.sector_share)), max_positions=max_positions,
        min_probability=min_probability, warning_factor=float(values.get("warning_factor", default.warning_factor)),
        good_factor=float(values.get("good_factor", default.good_factor)),
        playbook_cap=float(values.get("playbook_cap", default.playbook_cap)),
        tilt_cap=float(values.get("tilt_cap", default.tilt_cap)))
