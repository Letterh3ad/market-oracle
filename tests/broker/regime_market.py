"""Synthetic markets for the regime tests: seeded random walks sharing a market factor."""

import random
from dataclasses import replace
from datetime import date

from oracle.market.bars import Bar
from tests.analysts.bars import weekdays


def walk(symbol: str, returns: list[float], start: float = 100.0, days: list[date] | None = None) -> list[Bar]:
    days = days or weekdays(len(returns) + 1)
    closes, c = [start], start
    for r in returns:
        c *= 1 + r
        closes.append(c)
    return [Bar(symbol, d, c * 0.998, c * 1.01, c * 0.99, c, c, 1000, "USD", "test") for d, c in zip(days, closes)]


def market(listings: int, days: int, seed: int = 7, factor: list[float] | None = None) -> dict[str, list[Bar]]:
    """Each listing: its own drift and noise over a shared market factor."""
    rng = random.Random(seed)
    factor = factor or [0.0003 + 0.008 * rng.gauss(0, 1) for _ in range(days - 1)]
    out = {}
    for k in range(listings):
        drift, noise, beta = rng.uniform(-0.0006, 0.0012), rng.uniform(0.006, 0.02), rng.uniform(0.5, 1.5)
        out[f"L{k:03d}"] = walk(f"L{k:03d}", [drift + beta * f + noise * rng.gauss(0, 1) for f in factor])
    return out


def wild_after(history: dict[str, list[Bar]], day: date, extra: int = 30, seed: int = 99) -> dict[str, list[Bar]]:
    """Every bar after `day` replaced by wild ones (crashes, splits, spikes), plus `extra` more."""
    rng = random.Random(seed)
    out = {}
    for s, bars in history.items():
        kept = [b for b in bars if b.date <= day]
        after = len(bars) - len(kept) + extra
        days = weekdays(len(kept) + after, bars[0].date)[len(kept):]
        c = kept[-1].close
        for d in days:
            c *= rng.choice((0.3, 0.9, 1.0, 1.2, 3.0))
            kept.append(replace(kept[-1], date=d, open=c, high=c, low=c, close=c, adj_close=c))
        out[s] = kept
    return out
