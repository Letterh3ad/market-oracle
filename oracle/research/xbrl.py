"""SEC XBRL company facts, point in time (next-signals 04): the facts a signal needs from
`companyfacts`, the day each became known (its filing's acceptance day, else its filing date),
first-filed values per period (a restatement is not what was known then), quarterly series with
the fourth quarter derived from the year, and the per-company score events of the fundamental
signals. Numbers in, numbers out."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from oracle.research.edgar import new_york

COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
TAXONOMY = "us-gaap"
FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A", "10-QT", "10-QT/A"})

# Pre-registered fallbacks (DECISIONS 2026-10-01, ticket 04): per period, the first name with a value.
EPS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic")
GROSS_PROFIT = ("GrossProfit",)
REVENUE = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
           "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet")
COST = ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization")
ASSETS = ("Assets",)
UNITS = {**{c: "USD/shares" for c in EPS}, **{c: "USD" for c in (*GROSS_PROFIT, *REVENUE, *COST, *ASSETS)}}

QUARTER = (80, 100)
NINE_MONTHS = (260, 285)
YEAR = (350, 380)
"""Durations in days, both ends included."""
SEAM = 7
"""Days a quarter may start after the last one ended (52/53-week years)."""
SUE_HISTORY, SUE_MIN, SUE_REACH = 8, 4, 740
"""Changes before the current one its spread is taken from, the fewest it needs, and how far back they may end."""
HOLD = timedelta(days=200)
"""A value is not used once this old (a company that stopped filing)."""


@dataclass(frozen=True)
class Fact:
    concept: str
    unit: str
    start: date | None
    """None for an instant (a balance)."""
    end: date
    value: float
    accession: str
    form: str
    filed: date


def parse_companyfacts(body: object) -> list[Fact]:
    """The facts of the concepts in UNITS, in their unit, from the forms in FORMS."""
    gaap = body.get("facts", {}).get(TAXONOMY, {}) if isinstance(body, dict) else {}
    out = []
    for concept, unit in UNITS.items():
        rows = (gaap.get(concept) or {}).get("units", {}).get(unit) or []
        for r in rows:
            try:
                if r.get("form") not in FORMS or r.get("val") is None:
                    continue
                start = date.fromisoformat(r["start"]) if r.get("start") else None
                out.append(Fact(concept, unit, start, date.fromisoformat(r["end"]), float(r["val"]), str(r["accn"]),
                                r["form"], date.fromisoformat(r["filed"])))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def parse_acceptances(columns: object) -> dict[str, str]:
    """accession -> acceptance time (YYYY-MM-DDTHH:MM in New York; SEC's stamp is UTC) of the
    FORMS filings in one list of the submissions' parallel columns."""
    columns = columns if isinstance(columns, dict) else {}
    out = {}
    for form, accession, accepted in zip(*(columns.get(k, []) for k in ("form", "accessionNumber",
                                                                         "acceptanceDateTime"))):
        stamp = new_york(accepted)
        if form in FORMS and accession and stamp:
            out[accession] = stamp
    return out


def known_day(f: Fact, accepted: Mapping[str, str]) -> date:
    """The day the fact became public: its filing's acceptance day in New York, else its filing date (which
    is the next business day for a filing accepted after 17:30, so never earlier)."""
    stamp = accepted.get(f.accession)
    return date.fromisoformat(stamp[:10]) if stamp else f.filed


@dataclass(frozen=True)
class Period:
    start: date | None
    end: date
    value: float
    known: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1 if self.start else 0


Key = tuple[date | None, date]


def first_filed(facts: Iterable[Fact], accepted: Mapping[str, str]) -> dict[Key, Period]:
    """Per period, the value of its first filing: what was known then, whatever later restated."""
    out: dict[Key, tuple[tuple, Period]] = {}
    for f in facts:
        k = known_day(f, accepted)
        rank = (k, f.filed, f.accession)
        key = (f.start, f.end)
        if key not in out or rank < out[key][0]:
            out[key] = (rank, Period(f.start, f.end, f.value, k))
    return {key: p for key, (_, p) in out.items()}


def by_priority(facts: Sequence[Fact], names: Sequence[str], accepted: Mapping[str, str]) -> dict[Key, Period]:
    """Per period, the first-filed value of the first name in `names` that has one."""
    out: dict[Key, Period] = {}
    for name in names:
        for key, p in first_filed((f for f in facts if f.concept == name), accepted).items():
            out.setdefault(key, p)
    return out


def gross_profit(facts: Sequence[Fact], accepted: Mapping[str, str]) -> dict[Key, Period]:
    """GrossProfit per period, else revenue minus cost of revenue (each by priority), known when both are."""
    out = by_priority(facts, GROSS_PROFIT, accepted)
    rev, cost = by_priority(facts, REVENUE, accepted), by_priority(facts, COST, accepted)
    for key, r in rev.items():
        if key not in out and key in cost:
            c = cost[key]
            out[key] = Period(r.start, r.end, r.value - c.value, max(r.known, c.known))
    return out


def _within(n: int, span: tuple[int, int]) -> bool:
    return span[0] <= n <= span[1]


def _follows(prev_end: date, start: date | None) -> bool:
    return start is not None and 1 <= (start - prev_end).days <= SEAM


def quarters(periods: Mapping[Key, Period]) -> list[Period]:
    """Quarter-long periods by end, one per end (the earliest known), plus each fiscal year's
    fourth quarter derived as the year minus its first nine months (a nine-month figure, else its
    three quarters) where none is reported; a derived quarter is known when its last part is."""
    direct: dict[date, Period] = {}
    for p in periods.values():
        if p.start and _within(p.days, QUARTER) and (p.end not in direct or p.known < direct[p.end].known):
            direct[p.end] = p
    nine = [p for p in periods.values() if p.start and _within(p.days, NINE_MONTHS)]
    out = dict(direct)
    for y in periods.values():
        if not (y.start and _within(y.days, YEAR)) or y.end in out:
            continue
        parts = _first_nine(y, nine, direct)
        if parts:
            last = max(p.end for p in parts)
            if _within((y.end - last).days, QUARTER):
                out[y.end] = Period(last + timedelta(days=1), y.end, y.value - sum(p.value for p in parts),
                                    max([y.known, *(p.known for p in parts)]))
    return [out[e] for e in sorted(out)]


def _first_nine(y: Period, nine: list[Period], direct: Mapping[date, Period]) -> list[Period] | None:
    for n in nine:
        if abs((n.start - y.start).days) <= SEAM - 4:
            return [n]
    chain, end = [], None
    for _ in range(3):
        nxt = next((q for q in sorted(direct.values(), key=lambda q: q.end)
                    if (abs((q.start - y.start).days) <= SEAM - 4 if end is None else _follows(end, q.start))), None)
        if nxt is None:
            return None
        chain.append(nxt)
        end = nxt.end
    return chain


def trailing_year(qs: Sequence[Period]) -> list[Period]:
    """Sums of four consecutive quarters, at the last one's end, known when all four are."""
    out = []
    for k in range(3, len(qs)):
        four = qs[k - 3:k + 1]
        if all(_follows(a.end, b.start) for a, b in zip(four, four[1:])):
            out.append(Period(four[0].start, four[-1].end, sum(q.value for q in four), max(q.known for q in four)))
    return out


def instants(periods: Mapping[Key, Period]) -> dict[date, Period]:
    return {p.end: p for p in periods.values() if p.start is None}


@dataclass(frozen=True)
class Event:
    """A company's score from `known` on (decision days after it), until its next event or HOLD."""

    known: date
    end: date
    """The period it describes."""
    value: float | None
    """None: this filing's figure could not be scored, so the last score stops here."""


def _year_before(qs: Sequence[Period], q: Period) -> Period | None:
    best = None
    for p in qs:
        gap = (q.end - p.end).days
        if _within(gap, YEAR) and (best is None or abs(gap - 364) < abs((q.end - best.end).days - 364)):
            best = p
    return best


def sue_events(qs: Sequence[Period]) -> list[Event]:
    """Per quarter: its EPS minus the same quarter's a year before, over the sample standard
    deviation of the SUE_HISTORY such changes before it (SUE_MIN or more, ending within SUE_REACH
    days); known when every figure in it is."""
    changes: list[tuple[Period, float, date]] = []
    for q in qs:
        prior = _year_before(qs, q)
        if prior is not None:
            changes.append((q, q.value - prior.value, max(q.known, prior.known)))
    out = []
    for q in qs:
        mine = next((c for c in changes if c[0] is q), None)
        value, known = None, q.known
        if mine is not None:
            known = mine[2]
            past = [c for c in changes if c[0].end < q.end and (q.end - c[0].end).days <= SUE_REACH][-SUE_HISTORY:]
            if len(past) >= SUE_MIN:
                sd = _sd([c[1] for c in past])
                known = max([known, *(c[2] for c in past)])
                value = mine[1] / sd if sd > 0 else None
        out.append(Event(known, q.end, value))
    return out


def _sd(xs: Sequence[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def ratio_events(top: Sequence[Period], bottom: Mapping[date, Period]) -> list[Event]:
    """top / bottom at the same period end (bottom above 0), known when both are; a top without a
    usable bottom stops the last score."""
    out = []
    for t in top:
        b = bottom.get(t.end)
        ok = b is not None and b.value > 0
        out.append(Event(max(t.known, b.known) if ok else t.known, t.end, t.value / b.value if ok else None))
    return out


def value_events(periods: Sequence[Period]) -> list[Event]:
    return [Event(p.known, p.end, p.value) for p in periods]


def split_factor(splits: Sequence[tuple[date, float]], after: date) -> float:
    """Product of the split ratios (new shares per old) of splits after `after`: a per-share figure
    known then, divided by it, is in today's shares, like Yahoo's split-adjusted prices."""
    f = 1.0
    for day, ratio in splits:
        if day > after and ratio > 0:
            f *= ratio
    return f


def in_todays_shares(periods: Mapping[Key, Period], splits: Sequence[tuple[date, float]]) -> dict[Key, Period]:
    """Per-share figures in today's shares, each divided by the splits after its own known day:
    done before quarters() so a derived fourth quarter never mixes old and new shares."""
    return {k: Period(p.start, p.end, p.value / split_factor(splits, p.known), p.known) for k, p in periods.items()}


LATE = timedelta(days=180)
"""A period first known this long after it ended is an old comparative, not news."""


def timeline(events: Sequence[Event]) -> list[Event]:
    """Events by known day, each for a later period than every one known by then (a stale period
    first seen as a later filing's comparative, or one known later than LATE, is dropped)."""
    out, last = [], date.min
    for e in sorted(events, key=lambda e: (e.known, e.end)):
        if e.end > last and e.known - e.end <= LATE:
            if out and out[-1].known == e.known:
                out.pop()
            out.append(e)
            last = e.end
    return out
