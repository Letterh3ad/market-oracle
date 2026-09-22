"""Trend: where the price sits against its 50- and 200-day averages, and the 12-1 month
return (the past year excluding the last month, which tends to reverse)."""

from __future__ import annotations

from collections.abc import Sequence

from oracle.analysts.signals import AnalystSignal, Skipped
from oracle.market.bars import Bar

NAME = "trend"
YEAR = 252
MONTH = 21
# A 12-1 return this large, either way, counts as a full-strength move.
FULL_MOMENTUM = 0.20
# Within this of an average counts as sitting on it, not above or below.
FLAT_BAND = 0.01


def trend(bars: Sequence[Bar]) -> AnalystSignal | Skipped:
    if not bars:
        return Skipped(NAME, "", "no stored prices")
    symbol = bars[-1].symbol
    if len(bars) <= YEAR:
        return Skipped(NAME, symbol, f"needs a year of prices ({YEAR + 1} trading days), has {len(bars)}")
    closes = [b.adj_close if b.adj_close is not None else b.close for b in bars]
    close, sma50, sma200 = closes[-1], _mean(closes[-50:]), _mean(closes[-200:])
    momentum = closes[-MONTH - 1] / closes[-YEAR - 1] - 1
    parts = (
        _side(close, sma200),
        _side(sma50, sma200),
        max(-1.0, min(1.0, momentum / FULL_MOMENTUM)),
    )
    score = sum(parts) / len(parts)
    agreeing = sum(1 for p in parts if p * score > 0)
    return AnalystSignal(
        analyst=NAME, symbol=symbol, as_of=bars[-1].date,
        score=round(score, 4), confidence=round(agreeing / len(parts), 4) if score else 0.0,
        reason=_reason(parts, momentum),
        figures=(("close", close), ("sma50", sma50), ("sma200", sma200), ("momentum_12_1", momentum)),
    )


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _side(value: float, average: float) -> float:
    gap = value / average - 1
    return 0.0 if abs(gap) < FLAT_BAND else (1.0 if gap > 0 else -1.0)


def _reason(parts: tuple[float, float, float], momentum: float) -> str:
    where = {1.0: "above", -1.0: "below", 0.0: "at"}
    return (
        f"The price is {where[parts[0]]} its 200-day average, the 50-day average is "
        f"{where[parts[1]]} the 200-day, and over the past year (leaving out the last month) "
        f"it {'rose' if momentum >= 0 else 'fell'} {abs(momentum):.0%}."
    )
