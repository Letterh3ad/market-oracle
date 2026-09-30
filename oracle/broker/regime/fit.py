"""regime_wf's learned presets: each mood's method weights refitted from the methods' own
records (the health's pick windows) over a trailing window, then held fixed until the next
refit. Deliberately simple and fixed a priori, so the walk-forward tests the idea, not a search
over its knobs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from .playbook import MIN_RECORD
from .settings import METHODS, MOODS

TRAILING = timedelta(days=730)
"""Two years of pick days: long enough to see each mood, short enough to follow a change."""
BLEND = 0.5
"""The fitted half; the other half stays on the literature's preset, so one lucky stretch
never takes the whole weight."""
MIN_WINDOWS = MIN_RECORD
"""A method needs as many windows in a mood as its health needs before either means anything."""


@dataclass(frozen=True)
class Window:
    """One of a method's health windows: its top fifth picked on `day`, run to `closed`."""

    day: date
    closed: date
    mood: str
    """The market's mood on the pick day."""
    method: str
    excess: float
    """The picks' forward return over the composite's."""


Presets = Mapping[str, Mapping[str, float]]


def fit_presets(windows: Iterable[Window], presets: Presets, day: date) -> dict[str, dict[str, float]]:
    """Each mood's weights for trading from `day`, reading only windows closed before it and
    picked in the TRAILING span. In a mood, the methods with MIN_WINDOWS windows share their
    preset mass in proportion to their positive mean excess (a method behind the composite gets
    none); the rest keep their preset. The result is blended BLEND with the preset, so every
    weight is at least half its preset and each mood sums to 1. A mood with no method ahead of
    the composite, or not seen at all, keeps its preset."""
    since = day - TRAILING
    found: dict[tuple[str, str], list[float]] = defaultdict(list)
    for w in windows:
        if since <= w.day and w.closed < day:
            found[(w.mood, w.method)].append(w.excess)
    out = {}
    for mood in MOODS:
        preset = _normalised(presets[mood])
        means = {m: sum(v) / len(v) for m in METHODS if len(v := found.get((mood, m), ())) >= MIN_WINDOWS}
        ahead = {m: max(0.0, x) for m, x in means.items()}
        total = sum(ahead.values())
        if total <= 0:
            out[mood] = preset
            continue
        mass = sum(preset[m] for m in means)
        learned = {m: mass * ahead[m] / total if m in means else preset[m] for m in METHODS}
        out[mood] = {m: BLEND * learned[m] + (1 - BLEND) * preset[m] for m in METHODS}
    return out


def _normalised(weights: Mapping[str, float]) -> dict[str, float]:
    total = sum(weights.get(m, 0.0) for m in METHODS)
    return {m: weights.get(m, 0.0) / total if total > 0 else 0.0 for m in METHODS}
