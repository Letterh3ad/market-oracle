"""The factor baseline account's leans: hold the top N listings by 12-1 momentum (optionally
blended with low volatility), ranked on the last day of each month on closes up to that day.
Through the month the top N lean buy and the rest of the ranked set sell, so the rules rotate
out of names that left the top at the first chance and fill freed slots as cash allows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from oracle.analysts.price_factors import Ranked, ranked_at
from oracle.broker.backtest import LeanSource
from oracle.broker.rules import Lean, Rules
from oracle.market.bars import Bar

__all__ = ["SOURCE", "caption", "momentum_lean", "rank_day"]

SOURCE = "momentum"
RANK_STEP = 0.001


def rank_day(day: date) -> date:
    """The last day of the month before `day`'s: every lean in a month reads that day's ranks."""
    return day.replace(day=1) - timedelta(days=1)


def caption(rules: Rules, low_vol_weight: float = 0.0) -> str:
    """The top N is the position limit; at full size the cash floor may fit fewer."""
    top = rules.max_positions
    fits = min(top, int((1 - rules.cash_floor) / rules.max_position + 1e-9))
    held = f" (about {fits} at full size under the cash floor; risk sizing can hold more)" if fits < top else ""
    blend = f", blended {low_vol_weight:.0%} with low volatility," if low_vol_weight else ""
    return (f"Momentum: the top {top} listings{held} by 12-1 momentum (the year's return leaving out the last month)"
            f"{blend} ranked on each month's last day on closes up to it and held under the same rules, risk sizes "
            "and costs, leaders bought first; a listing that leaves the top is sold, a stop-out is bought back while "
            "it stays in the top, and one with a price break in its year is not ranked.")


def momentum_lean(history: Mapping[str, Sequence[Bar]], top: int, low_vol_weight: float = 0.0) -> LeanSource:
    """`history` is every listing's bars, oldest first; ranks read only bars on or before each
    rank day (`ranked_at`), so later bars in it are never seen. A buy's probability is just
    under 1.0 and falls with rank (`RANK_STEP`), so when cash or slots run out the rules, which buy
    by probability, buy the leaders first."""
    picks: dict[date, dict[str, int]] = {}

    def at(day: date) -> dict[str, int]:
        if day not in picks:
            scores = {s: v for s, r in ranked_at(history, day).items() if (v := _score(r, low_vol_weight)) is not None}
            picks[day] = {s: i for i, s in enumerate(sorted(scores, key=lambda s: (-scores[s], s)))}
        return picks[day]

    def lean(symbol: str, bars: Sequence[Bar]) -> Lean | None:
        day = rank_day(bars[-1].date)
        position = at(day).get(symbol)
        if position is None:
            return None
        if position >= top:
            return Lean(symbol, day, "sell", 1.0, SOURCE)
        return Lean(symbol, day, "buy", 1.0 - RANK_STEP * position, SOURCE)

    return lean


def _score(r: Ranked, low_vol_weight: float) -> float | None:
    if r.momentum_pct is None:
        return None
    if not low_vol_weight:
        return r.momentum_pct
    if r.low_vol_pct is None:
        return None
    return (1 - low_vol_weight) * r.momentum_pct + low_vol_weight * r.low_vol_pct
