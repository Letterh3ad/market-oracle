"""The regime lines' vocabulary and knobs: the methods blended, the moods read, the preset
weights (priors from the literature, overridable in `[regime]`) and the non-price inputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
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
class Rotation:
    """regime-v2's way out of a stale holding: without it the book sells only on a sell lean or
    the stop, so it kept whatever looked steady early on (gate 03 round 1). Fixed a priori, never
    fitted to a backtest."""

    min_hold: int = 20
    """Trading days (the listing's own bars) a holding is kept before it can be rotated out."""
    margin: float = 0.25
    """How far under its candidate's blended score a holding must be."""
    most: int = 2
    """Rotations a step, so one noisy day never turns the book over."""


@dataclass(frozen=True)
class RegimeSettings:
    weights: Mapping[str, Mapping[str, float]] = field(default_factory=lambda: PRESETS)
    """mood -> method -> preset weight; each mood's row is renormalised."""
    span: int = 5
    """Trading days the final weights are smoothed over (exponential), so one noisy day never flips the book."""
    sector_share: float = 0.3
    """At most ceil(this x max_positions) buys plus holdings per sector."""
    max_positions: int = 10
    min_probability: float = field(kw_only=True)
    """The rules' buy threshold: every buy lean clears it, so the label alone decides a buy.
    Required, so a book never leans against another threshold than its account's."""
    warning_factor: float = 0.5
    good_factor: float = 1.25
    playbook_cap: float = 1.5
    tilt_cap: float = 0.5
    """The AI tilt multiplies each method's weight within [1 - cap, 1 + cap]."""
    spread_unscored: bool = False
    """regime-v2: a method with no score for a listing hands its weight to the price methods
    present, in proportion to theirs, rather than to every present method, where the constant
    hold score took a quarter to a third of the blend."""
    rotation: Rotation | None = None
    """regime-v2's rotation; None (v1) sells only on a sell lean or the stop."""


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
    extra: Mapping[str, Mapping[str, DatedScores]] = field(default_factory=dict)
    """Extra methods by name (not one of METHODS): symbol -> dated scores in [-1, 1], oldest first
    (they are searched by day, so an unsorted series reads the wrong score), each usable
    from its day for `extra_max_age` days. A method weighs by its name's weight in each mood of
    the settings, none by default (Laya joins the core-satellite this way)."""
    extra_max_age: int = 7


def settings_from(values: Mapping[str, object], *, max_positions: int, min_probability: float) -> RegimeSettings:
    """From the flat `[regime]` config (`<mood>_<method>` weight keys beside the others)."""
    weights = {m: {k: float(values.get(f"{m}_{k}", PRESETS[m][k])) for k in METHODS} for m in MOODS}
    default = RegimeSettings(min_probability=min_probability)
    return RegimeSettings(
        weights=weights, span=int(values.get("smoothing_span", default.span)),
        sector_share=float(values.get("sector_share", default.sector_share)), max_positions=max_positions,
        min_probability=min_probability, warning_factor=float(values.get("warning_factor", default.warning_factor)),
        good_factor=float(values.get("good_factor", default.good_factor)),
        playbook_cap=float(values.get("playbook_cap", default.playbook_cap)),
        tilt_cap=float(values.get("tilt_cap", default.tilt_cap)))


def v2_settings(settings: RegimeSettings, values: Mapping[str, object] | None = None) -> RegimeSettings:
    """The regime-v2 book's settings: the v1 book's with both changes, the rotation from the flat
    `[regime_v2]` config when given."""
    return replace(settings, spread_unscored=True, rotation=rotation_from(values or {}))


def rotation_from(values: Mapping[str, object]) -> Rotation:
    default = Rotation()
    return Rotation(int(values.get("rotation_min_hold_days", default.min_hold)),
                    float(values.get("rotation_margin", default.margin)),
                    int(values.get("rotation_max", default.most)))
