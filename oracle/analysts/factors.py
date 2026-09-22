"""Value, quality and growth: each of the company's figures as a percentile within its
peer group, averaged per factor."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from oracle.analysts.signals import AnalystSignal, Skipped
from oracle.market.fundamentals import Fundamentals

# Peers reporting a figure below this make the comparison weak.
ENOUGH_PEERS = 5


@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    higher_is_better: bool
    negative_is_worst: bool = False
    """For ratios where a negative value means losses or negative equity, not cheapness."""


@dataclass(frozen=True)
class Factor:
    name: str
    verb: str
    meaning: str
    metrics: tuple[Metric, ...]


FACTORS = (
    Factor("value", "cheaper than",
           "How cheap the shares are for what the company earns, owns and pays out, against its peers.",
           (Metric("pe", "P/E", False, True), Metric("forward_pe", "forward P/E", False, True),
            Metric("pb", "P/B", False, True), Metric("dividend_yield", "dividend yield", True))),
    Factor("quality", "stronger than",
           "How profitable and soundly financed the business is, against its peers.",
           (Metric("profit_margin", "profit margin", True), Metric("roe", "return on equity", True),
            Metric("debt_to_equity", "debt to equity", False, True))),
    Factor("growth", "growing faster than",
           "How fast the company's sales are growing, against its peers.",
           (Metric("revenue_growth", "revenue growth", True),)),
)


def percentile(value: float, others: Sequence[float], higher_is_better: bool) -> float:
    """Share of `others` this value beats, ties counting half."""
    beats = sum(1 for o in others if (value > o if higher_is_better else value < o))
    ties = sum(1 for o in others if o == value)
    return (beats + ties / 2) / len(others)


def value(company: Fundamentals, peers: Sequence[Fundamentals], basis: str) -> AnalystSignal | Skipped:
    return _score(FACTORS[0], company, peers, basis)


def quality(company: Fundamentals, peers: Sequence[Fundamentals], basis: str) -> AnalystSignal | Skipped:
    return _score(FACTORS[1], company, peers, basis)


def growth(company: Fundamentals, peers: Sequence[Fundamentals], basis: str) -> AnalystSignal | Skipped:
    return _score(FACTORS[2], company, peers, basis)


def _score(factor: Factor, company: Fundamentals, peers: Sequence[Fundamentals], basis: str) -> AnalystSignal | Skipped:
    rows = []
    for m in factor.metrics:
        mine = getattr(company, m.name)
        others = [v for p in peers if (v := getattr(p, m.name)) is not None]
        if mine is None or not others:
            continue
        rank = _comparable(m)
        rows.append((m, percentile(rank(mine), [rank(o) for o in others], m.higher_is_better), len(others)))
    if not rows:
        wanted = ", ".join(m.label for m in factor.metrics)
        return Skipped(factor.name, company.symbol, f"no {wanted} for the company and its peers to compare")
    average = sum(p for _, p, _ in rows) / len(rows)
    score = 2 * average - 1
    agree = sum(1 for _, p, _ in rows if (p - 0.5) * score > 0) / len(rows) if score else 0.0
    thin = sum(min(1.0, n / ENOUGH_PEERS) for _, _, n in rows) / len(rows)
    parts = [f"{_count(p * n)} of {n}{' peers' if i == 0 else ''} on {m.label}" for i, (m, p, n) in enumerate(rows)]
    reason = f"{factor.name.capitalize()} against {basis}: {factor.verb} {', '.join(parts)}."
    weak = [f"only {n} peers report {m.label}" for m, _, n in rows if n < ENOUGH_PEERS]
    if weak:
        reason += f" A weak comparison: {'; '.join(weak)}."
    figures = tuple((f"{m.name}_percentile", round(p, 4)) for m, p, _ in rows) + (("peers", float(len(peers))),)
    return AnalystSignal(factor.name, company.symbol, company.as_of, round(score, 4), round(agree * thin, 4),
                         reason, figures)


def _comparable(m: Metric):
    if not m.negative_is_worst:
        return lambda v: v
    worst = float("-inf") if m.higher_is_better else float("inf")
    return lambda v: worst if v < 0 else v


def _count(x: float) -> str:
    return f"{x:g}"
