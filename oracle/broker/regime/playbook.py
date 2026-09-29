"""The playbook and the shadow records. Signs from the literature that a method is about to
do badly (warnings) or well (good signs) in the day's market, each a pure test of the mood
and the methods' picks; and each method's own record, the forward return of its top fifth
over the composite, as a multiplier on its weight."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from .mood import Mood

BIG_YEAR = 0.20
"""A composite year this far up or down is one of the extreme years trend-following earns in."""
CROWDED = 0.5
"""Momentum's picks over this share in one sector are one sector bet, not momentum."""
MIN_CROWD = 4
"""Picks needed before their sector split means anything."""

HORIZON = 20
"""Trading days each pick's forward return runs."""
RECORD = 60
"""Most recent completed forward windows a method's record reads."""
MIN_RECORD = 20
"""Windows needed before a record moves the weight at all."""
HEALTH_SCALE = 0.05
"""A mean 20-day excess this large (about 60% a year) reaches the multiplier's limit."""
HEALTH_RANGE = (0.5, 1.5)
MIN_SCORED = 5
"""Listings a method must score on a day for its top fifth to be a pick."""


@dataclass(frozen=True)
class Day:
    mood: Mood
    picks: Mapping[str, Sequence[str]]
    """method -> its top-fifth listings on the day."""
    sectors: Mapping[str, str]


@dataclass(frozen=True)
class Sign:
    method: str
    name: str
    warning: bool
    reason: str
    source: str
    fires: Callable[[Day], bool]


def _flag(name: str) -> Callable[[Day], bool]:
    return lambda d: name in d.mood.flags


def _mood(name: str) -> Callable[[Day], bool]:
    return lambda d: d.mood.mood == name


def _crowded(d: Day) -> bool:
    picks = d.picks.get("momentum", ())
    top = Counter(d.sectors[s] for s in picks if s in d.sectors).most_common(1)
    return len(picks) >= MIN_CROWD and bool(top) and top[0][1] > CROWDED * len(picks)


SIGNS: tuple[Sign, ...] = (
    Sign("trend", "choppy", True, "The composite keeps crossing its average: trend signals whipsaw.",
         "Hurst-Ooi-Pedersen 2017", _flag("choppy")),
    Sign("trend", "rebound", True,
         "A sharp month after a losing year: slow trend signals are on the wrong side of the turn.",
         "Garg-Goulding-Harvey-Mazzoleni 2021", _flag("rebound")),
    Sign("trend", "big_year", False, "An extreme year either way: time-series momentum earns most in extreme markets.",
         "Moskowitz-Ooi-Pedersen 2012", lambda d: abs(d.mood.readings["ret_12m"]) >= BIG_YEAR),
    Sign("momentum", "rebound", True, "Momentum crashes when a bear market snaps back: the past losers lead.",
         "Daniel-Moskowitz 2016", _flag("rebound")),
    Sign("momentum", "volatile_down", True, "A falling, volatile market is the panic state where momentum crashes.",
         "Daniel-Moskowitz 2016", _mood("volatile_down")),
    Sign("momentum", "clustered", True, "Listings moving together: crowded trades that unwind together.",
         "Lou-Polk 2022", _flag("clustered")),
    Sign("momentum", "sector_crowded", True,
         "Over half the picks in one sector: an industry bet that can reverse as one.",
         "Moskowitz-Grinblatt 1999", _crowded),
    Sign("reversion", "volatile_down", True, "In a falling panic dips keep dipping: RSI-2 buys only above the average.",
         "Connors-Alvarez 2009", _mood("volatile_down")),
    Sign("reversion", "volatile_up", False,
         "Short-term reversal pays most when volatility is high: liquidity is scarce.",
         "Nagel 2012", _mood("volatile_up")),
    Sign("low_vol", "rebound", True, "Calm, low-beta listings lag a risk-on snap-back.",
         "Frazzini-Pedersen 2014", _flag("rebound")),
    Sign("quality_value", "rebound", False, "Value moves against momentum and leads out of a bear market.",
         "Asness-Moskowitz-Pedersen 2013", _flag("rebound")),
    Sign("quality_value", "volatile_down", False, "Quality is where money flies in a falling, volatile market.",
         "Asness-Frazzini-Pedersen 2019", _mood("volatile_down")),
)
"""Earnings drift, buy-and-hold and the AI verdict have no sign: no study ties them to these readings."""


def firing(day: Day) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """(method -> firing warnings, method -> firing good signs), each in the playbook's order."""
    warnings: dict[str, tuple[str, ...]] = {}
    good: dict[str, tuple[str, ...]] = {}
    for sign in SIGNS:
        if sign.fires(day):
            into = warnings if sign.warning else good
            into[sign.method] = (*into.get(sign.method, ()), sign.name)
    return warnings, good


def top_fifth(scores: Mapping[str, float]) -> tuple[str, ...]:
    """The fifth with the highest scores, ties broken by symbol; none under MIN_SCORED listings or when
    every score is the same (buy-and-hold's constant), where a top fifth picks nothing."""
    if len(scores) < MIN_SCORED or len(set(scores.values())) < 2:
        return ()
    ranked = sorted(scores, key=lambda s: (-scores[s], s))
    return tuple(ranked[:math.ceil(len(ranked) / 5)])


def health(record: Sequence[float]) -> float:
    """A method's multiplier from its record of forward excess returns; neutral with too little."""
    if len(record) < MIN_RECORD:
        return 1.0
    low, high = HEALTH_RANGE
    return min(high, max(low, 1 + sum(record) / len(record) / HEALTH_SCALE))
