"""Indicator arithmetic over a list of closes, oldest first. Each series has the input's
length, with None until enough data exists."""

from __future__ import annotations

import math
from collections.abc import Sequence

from oracle.market.bars import Bar


def closes(bars: Sequence[Bar]) -> list[float]:
    """Adjusted where known, so dividends and splits do not read as price drops."""
    return [b.adj_close if b.adj_close is not None else b.close for b in bars]


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def rsi(closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI: a simple average of the first `period` gains and losses, then his
    smoothing (each new average keeps (period - 1) / period of the old)."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = mean([max(c, 0.0) for c in changes[:period]])
    loss = mean([max(-c, 0.0) for c in changes[:period]])
    out[period] = _rsi(gain, loss)
    for i, c in enumerate(changes[period:], start=period + 1):
        gain = (gain * (period - 1) + max(c, 0.0)) / period
        loss = (loss * (period - 1) + max(-c, 0.0)) / period
        out[i] = _rsi(gain, loss)
    return out


def _rsi(gain: float, loss: float) -> float:
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100 - 100 / (1 + gain / loss)


def ema(values: Sequence[float | None], period: int) -> list[float | None]:
    """Seeded with the simple average of the first `period` values present."""
    out: list[float | None] = [None] * len(values)
    start = next((i for i, v in enumerate(values) if v is not None), len(values))
    if len(values) - start < period:
        return out
    alpha = 2 / (period + 1)
    seed_at = start + period - 1
    out[seed_at] = mean(values[start:seed_at + 1])
    for i in range(seed_at + 1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """(MACD line, signal line, histogram)."""
    f, s = ema(closes, fast), ema(closes, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    sig = ema(line, signal)
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def volatility(closes: Sequence[float], window: int = 20, per_year: int = 252) -> list[float | None]:
    """Annualised sample standard deviation of daily log returns over `window` returns."""
    out: list[float | None] = [None] * len(closes)
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    for i in range(window, len(closes)):
        chunk = returns[i - window:i]
        m = mean(chunk)
        out[i] = math.sqrt(sum((r - m) ** 2 for r in chunk) / (window - 1) * per_year)
    return out
