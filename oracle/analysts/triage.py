"""The research funnel's first two stages in numbers (research-lab 05): a published long-short
portfolio's monthly returns over a span (mean, t, Sharpe), the McLean-Pontiff publication split,
the literature's effect turned into an IC, and the smallest effect the testbed can see. Numbers in,
numbers out; returns are in percent a month, as Open Source Asset Pricing publishes them."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from statistics import NormalDist, median

__all__ = ["MONTH_DAYS", "SIGMA_CS", "STRATEGY_Z", "Detectable", "Span", "Split", "detectable_excess",
           "detectable_ic", "ic_from_spread", "publication_split", "span"]

MONTH_DAYS = 21
"""Trading days in a month: OSAP's monthly spread is a 21-day horizon."""
SIGMA_CS = 0.08
"""Cross-sectional spread (standard deviation) of large US stocks' monthly returns, about 8%."""
STRATEGY_Z = NormalDist().inv_cdf(0.95)
"""A strategy's bar is a deflated Sharpe probability of 0.95: one-sided z 1.645 with one variant."""


@dataclass(frozen=True)
class Span:
    label: str
    start: str | None
    """First month with a return (YYYY-MM); None when there is none."""
    end: str | None
    n: int
    mean: float | None
    """Percent a month."""
    sd: float | None
    t: float | None
    sharpe: float | None
    """Annualised: mean / sd * sqrt(12)."""


@dataclass(frozen=True)
class Split:
    spans: tuple[Span, ...]
    """In sample, after the sample before publication, after publication."""
    decay: float | None
    """The share of the in-sample mean lost after publication."""


@dataclass(frozen=True)
class Detectable:
    value: float
    used: int
    """Kept confirm windows it is the median of."""
    same_horizon: int
    """Of those, the ones at the asked horizon (only they are used when there are any)."""


def span(label: str, rets: Mapping[str, float], start: str | None, end: str | None) -> Span:
    """The months from `start` to `end` (YYYY-MM, inclusive; None is open)."""
    months = sorted(m for m in rets if (start is None or m >= start) and (end is None or m <= end))
    vals = [rets[m] for m in months]
    n = len(vals)
    if not n:
        return Span(label, None, None, 0, None, None, None, None)
    mean = sum(vals) / n
    if n < 2:
        return Span(label, months[0], months[-1], n, mean, None, None, None)
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
    t = mean / (sd / math.sqrt(n)) if sd else None
    return Span(label, months[0], months[-1], n, mean, sd, t, mean / sd * math.sqrt(12) if sd else None)


def publication_split(rets: Mapping[str, float], sample_start: int, sample_end: int, published: int) -> Split:
    """McLean and Pontiff (2016): the paper's sample years, the years after it up to the publication
    year, and every month after the publication year."""
    spans = (span("in sample", rets, f"{sample_start}-01", f"{sample_end}-12"),
             span("after the sample, before publication", rets, f"{sample_end + 1}-01", f"{published}-12"),
             span("after publication", rets, f"{published + 1}-01", None))
    inside, after = spans[0].mean, spans[2].mean
    return Split(spans, 1 - after / inside if inside and after is not None else None)


def ic_from_spread(monthly_pct: float, quantile: float, h: int, sigma: float = SIGMA_CS) -> float:
    """The IC a score needs to spread its top and bottom `quantile` tails by `monthly_pct` a month
    (Grinold and Kahn: spread = IC * sigma * 2 phi(z) / q for normal scores), at `h` trading days:
    the spread grows with h and the cross-section's spread with its root."""
    z = NormalDist().inv_cdf(1 - quantile)
    gap = 2 * NormalDist().pdf(z) / quantile
    return monthly_pct / 100 / (sigma * gap) * math.sqrt(h / MONTH_DAYS)


def detectable_ic(observed: Iterable[tuple[int, float | None, float | None]], h: int, bar: float) -> Detectable | None:
    """The smallest IC the testbed sees at t `bar` and horizon `h`, from kept confirm windows'
    (horizon, IC, t): each one's own standard error |IC / t| (cross-name correlation included, which
    the names-and-days formula leaves out), scaled by root h from its horizon, median, times the bar."""
    usable = [(oh, abs(ic / t)) for oh, ic, t in observed if ic is not None and t]
    if not usable:
        return None
    same = [se for oh, se in usable if oh == h]
    chosen = same or [se * math.sqrt(h / oh) for oh, se in usable]
    return Detectable(bar * median(chosen), len(chosen), len(same))


def detectable_excess(tracking_error: float, years: float, z: float = STRATEGY_Z) -> float:
    """The smallest yearly excess over the index a strategy's confirm window sees: z tracking
    errors over the root of its years."""
    return z * tracking_error / math.sqrt(years)
