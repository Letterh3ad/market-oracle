"""The research lab's harness numbers (research-lab spec 6): the PASS bar over every registered
test, and the deflated Sharpe ratio (Bailey and Lopez de Prado 2014) that discounts a backtest's
Sharpe for the variants tried before it. Numbers in, numbers out."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist

__all__ = ["ALPHA", "DSR_BAR", "T_FLOOR", "Moments", "bonferroni_bar", "deflated_sharpe", "expected_max_sharpe",
           "moments", "probabilistic_sharpe"]

T_FLOOR = 3.0
ALPHA = 0.05
DSR_BAR = 0.95
"""A strategy test's deflated Sharpe probability must reach this (spec 6)."""
EULER = 0.5772156649015329
_N = NormalDist()


def bonferroni_bar(n: int) -> float:
    """The t a test must reach with `n` registered hypotheses (each horizon one): the two-sided
    Bonferroni 5% critical value, never under T_FLOOR."""
    if n < 1:
        raise ValueError(f"at least one registered test, not {n}")
    return max(T_FLOOR, _N.inv_cdf(1 - ALPHA / (2 * n)))


@dataclass(frozen=True)
class Moments:
    """A return series' per-period Sharpe and shape, all the deflated Sharpe needs."""
    sharpe: float
    n: int
    skew: float
    kurtosis: float
    """Not excess: 3 for a normal series."""


def moments(returns: Sequence[float]) -> Moments | None:
    """None under 3 returns or without a spread."""
    n = len(returns)
    if n < 3:
        return None
    m = sum(returns) / n
    d = [r - m for r in returns]
    var = sum(x * x for x in d) / n
    if var <= 1e-24:
        return None
    sd = math.sqrt(var)
    return Moments(m / sd, n, sum(x ** 3 for x in d) / n / sd ** 3, sum(x ** 4 for x in d) / n / var ** 2)


def probabilistic_sharpe(m: Moments, benchmark: float) -> float | None:
    """The chance the true per-period Sharpe is above `benchmark`, allowing for the sample's
    length, skew and fat tails; None when the shape makes the spread undefined."""
    spread = 1 - m.skew * m.sharpe + (m.kurtosis - 1) / 4 * m.sharpe ** 2
    if spread <= 0:
        return None
    return _N.cdf((m.sharpe - benchmark) * math.sqrt(m.n - 1) / math.sqrt(spread))


def expected_max_sharpe(variants: int, variance: float) -> float:
    """The best per-period Sharpe expected among `variants` independent tries with no skill whose
    Sharpes spread with `variance` (the False Strategy Theorem); 0 for one try."""
    if variants < 1:
        raise ValueError(f"at least one variant, not {variants}")
    if variants == 1:
        return 0.0
    return math.sqrt(variance) * ((1 - EULER) * _N.inv_cdf(1 - 1 / variants)
                                  + EULER * _N.inv_cdf(1 - 1 / (variants * math.e)))


def deflated_sharpe(m: Moments, variants: int, variance: float | None = None) -> float | None:
    """The probabilistic Sharpe against the best Sharpe luck alone would give over `variants`
    tries. `variance` is the tries' Sharpe spread; unknown, a null Sharpe's sampling variance
    over the same length, 1 / (n - 1)."""
    v = 1 / (m.n - 1) if variance is None else variance
    return probabilistic_sharpe(m, expected_max_sharpe(variants, v))
