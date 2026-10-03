"""The numbers of a typed-event study (next-signals 05): each event's cumulative sector-excess
return from the decision day's open, and per window the mean with its t clustered by decision day.
Numbers in, numbers out."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from oracle.analysts.signal_test import Panel, excess

__all__ = ["HORIZONS", "PRIMARY", "Car", "EventRow", "clustered_t", "event_cars", "passes", "summarise"]

HORIZONS = (1, 5, 20, 60)
PRIMARY = 20
T_BAR = 2.0


@dataclass(frozen=True)
class EventRow:
    horizon: int
    n: int
    """Events with a return at this horizon."""
    days: int
    """Distinct decision days among them (the clusters)."""
    mean: float | None
    t: float | None
    positive: int


Car = tuple[date, float]
"""(decision day, sector-excess return)."""


def event_cars(p: Panel, events: Mapping[str, Sequence[int]], horizons: Sequence[int] = HORIZONS
               ) -> dict[int, list[tuple[int, float]]]:
    """horizon -> (decision day index, sector-excess return open i to open i + h) of each event,
    `events` being ticker -> decision day indexes. An event counts only on a day its ticker is a
    member with a return; the benchmark is that day's other members, as the signal harness's."""
    by_day: dict[int, list[str]] = {}
    for s, days in events.items():
        for i in days:
            if s in p.members[i]:
                by_day.setdefault(i, []).append(s)
    out: dict[int, list[tuple[int, float]]] = {h: [] for h in horizons}
    for i in sorted(by_day):
        for h in horizons:
            rets = {s: r for s in p.members[i] if (r := p.forward(s, i, h)) is not None}
            ex = excess(rets, p.sectors)
            out[h] += [(i, ex[s]) for s in by_day[i] if s in ex]
    return out


def clustered_t(cars: Sequence[Car]) -> tuple[float | None, float | None]:
    """The mean and its t with the standard error clustered by decision day (G/(G-1) factor)."""
    n = len(cars)
    if not n:
        return None, None
    m = sum(x for _, x in cars) / n
    groups: dict[date, float] = {}
    for d, x in cars:
        groups[d] = groups.get(d, 0.0) + (x - m)
    g = len(groups)
    if g < 2:
        return m, None
    var = g / (g - 1) * sum(v * v for v in groups.values()) / (n * n)
    return m, (m / math.sqrt(var) if var > 1e-18 else None)


def summarise(cars: Sequence[Car], h: int) -> EventRow:
    m, t = clustered_t(cars)
    return EventRow(h, len(cars), len({d for d, _ in cars}), _r(m), _r(t, 2), sum(x > 0 for _, x in cars))


def passes(train: EventRow | None, confirm: EventRow | None, bar: float = T_BAR, inclusive: bool = False) -> bool:
    """The confirm mean's t above `bar` (or at it, `inclusive`) in the train mean's sign."""
    if train is None or confirm is None or not train.mean or confirm.t is None:
        return False
    t = math.copysign(1, train.mean) * confirm.t
    return t >= bar if inclusive else t > bar


def _r(x: float | None, places: int = 5) -> float | None:
    return None if x is None else round(x, places)
