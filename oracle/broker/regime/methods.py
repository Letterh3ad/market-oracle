"""Each method's score for one listing on one day, in [-1, 1], or None for no score. Every
series here is built once over a listing's whole history but is rolling, so the value at a
bar reads only that bar and the ones before it."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from oracle.analysts.breaks import first_break
from oracle.analysts.indicators import closes, rolling_mean, rsi
from oracle.analysts.price_factors import Ranked
from oracle.market.bars import Bar

from .settings import DatedScores

AVERAGE = 200
SLOPE_DAYS = 20
TREND_SCALE = 0.10
"""A close this far above its 200-day average scores the full +1."""
RSI_LOW, RSI_HIGH = 10, 90
DRIFT_DAYS = 60
DRIFT_SCALE = 0.05
"""An earnings-day move this far over the composite scores the full +1."""
REACTION_GAP = timedelta(days=7)
"""A reaction bar further than this after the release is not its reaction."""
FACTOR_MAX_AGE = timedelta(days=366)
"""Value and quality read the latest annual accounts; older than a year they say nothing."""
HOLD_SCORE = 0.3


@dataclass
class Listing:
    """One listing's rolling series, index-aligned with its bars."""

    symbol: str
    dates: list[date]
    closes: list[float]
    average: list[float | None]
    slope: list[float | None]
    rsi2: list[float | None]
    clean_from: list[int]
    """Index of the last break at or before each bar (0 with none): bars before it are on another basis."""
    reactions: list[tuple[int, float]]
    """(reaction bar index, move over the composite) per earnings release, by index."""

    def at(self, day: date) -> int | None:
        i = bisect_right(self.dates, day) - 1
        return i if i >= 0 else None

    def settled(self, i: int) -> bool:
        """The average and its slope read no bar from before a break."""
        return self.average[i] is not None and self.slope[i] is not None and \
            i - self.clean_from[i] >= AVERAGE + SLOPE_DAYS - 1


def listing(symbol: str, bars: Sequence[Bar]) -> Listing:
    cs = closes(bars)
    average = rolling_mean(cs, AVERAGE)
    slope = [None if i < SLOPE_DAYS or a is None or average[i - SLOPE_DAYS] is None else a - average[i - SLOPE_DAYS]
             for i, a in enumerate(average)]
    clean, last = [], 0
    for i in range(len(cs)):
        if first_break(cs[max(0, i - 1):i + 1]) is not None:
            last = i
        clean.append(last)
    return Listing(symbol, [b.date for b in bars], cs, average, slope, rsi(cs, 2), clean, [])


def with_reactions(x: Listing, releases: Sequence[tuple[date, bool | None]],
                   composite_return: Callable[[date], float | None]) -> Listing:
    """A before-open release moves its own day's close; any other the next bar's."""
    out = []
    for day, before_open in releases:
        r = bisect_right(x.dates, day - timedelta(days=1)) if before_open else bisect_right(x.dates, day)
        if r < 1 or r >= len(x.dates) or x.dates[r] - day > REACTION_GAP or x.clean_from[r] == r:
            continue
        market = composite_return(x.dates[r])
        if market is None:
            continue
        out.append((r, x.closes[r] / x.closes[r - 1] - 1 - market))
    x.reactions = sorted(set(out))
    return x


def trend(x: Listing, i: int) -> float | None:
    """Moskowitz-Ooi-Pedersen: above a rising average is up; an average sloping against the
    gap halves it."""
    if not x.settled(i):
        return None
    gap = x.closes[i] / x.average[i] - 1
    score = _clip(gap / TREND_SCALE)
    return score * 0.5 if gap * x.slope[i] < 0 else score


def reversion(x: Listing, i: int) -> float | None:
    """Connors RSI-2: buy a sharp dip inside an uptrend, lean against a sharp spike."""
    if not x.settled(i) or x.rsi2[i] is None:
        return None
    if x.rsi2[i] < RSI_LOW and x.closes[i] > x.average[i]:
        return 1.0
    return -0.5 if x.rsi2[i] > RSI_HIGH else 0.0


def earnings_drift(x: Listing, i: int) -> float | None:
    """Bernard-Thomas: the reaction's direction persists, fading linearly over DRIFT_DAYS bars."""
    k = bisect_right(x.reactions, (i, float("inf"))) - 1
    if k < 0:
        return None
    r, move = x.reactions[k]
    age = i - r
    return _clip(move / DRIFT_SCALE) * (1 - age / DRIFT_DAYS) if age < DRIFT_DAYS else None


def momentum(ranked: Ranked | None) -> float | None:
    return None if ranked is None or ranked.momentum_pct is None else 2 * ranked.momentum_pct - 1


def low_vol(ranked: Ranked | None) -> float | None:
    return None if ranked is None or ranked.low_vol_pct is None else 2 * ranked.low_vol_pct - 1


def dated(scores: DatedScores, day: date, max_age: timedelta) -> float | None:
    """The newest score known on or before `day`, if no older than `max_age`."""
    k = bisect_right(scores, day, key=lambda x: x[0]) - 1
    if k < 0 or day - scores[k][0] > max_age:
        return None
    return _clip(scores[k][1])


def _clip(v: float) -> float:
    return max(-1.0, min(1.0, v))
