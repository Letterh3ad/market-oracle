"""How the replay study's verdicts did: each against its listing's price over the next one and
three months, and against the study's listings held in equal weight over the same months.
Scoring reads stored bars only, so the status poll can afford it; fetching the missing bars
is `update_study_prices`, run by the report command, the card's button and the tick."""

from __future__ import annotations

import calendar
import json
import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from oracle.market.bars import Bar
from oracle.research.trace import VERDICTS
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.prices_service import ListingOutcome, update_listings
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.meta import MetaRepo
from oracle.store.replay import DONE, FAILED, CellRow, ReplayRepo, StudyRow
from oracle.store.research import ResearchRepo

__all__ = ["LIMITS", "Score", "replay_report", "score_study", "update_study_prices", "wilson"]

HORIZONS: tuple[tuple[str, int], ...] = (("1m", 1), ("3m", 3))
"""Label and months ahead."""
BAND_EDGES = (0.6, 0.8)
BANDS = ("under 0.60", "0.60 to 0.80", "0.80 and up")
STALE = timedelta(days=7)
"""A close further than this from the day it stands for is a gap in the bars, not that day's price."""
SPLIT_RATIOS = (0.5, 2.0)
"""A day-over-day move outside these is taken for a split the stored bars were not re-based for."""
MIN_N = 10
"""Fewer hits and misses than this get no interval: it would span most of 0 to 1."""
Z95 = 1.96
SCORED, PENDING, UNPRICED = "scored", "pending", "unpriced"
PRICES_AT = "replay_prices_at"
_SIGN = {"buy": 1, "hold": 0, "sell": -1}

LIMITS = (
    "Replays read filings and dated headlines only; GDELT covers about 3 months, so older dates lean on "
    "Google News.",
    "The listings and their fundamentals are today's (survivorship): companies that failed since are not "
    "in the universe.",
    "About 300 samples over about 15 dates detect a clear edge or a clear lack of one, not a small one.",
    "Interim results, before every replay has run and every window has closed, are noisier still.",
    "Returns run from the first close after the as-of day (news that day may land after its close), in each "
    "listing's currency, from stored closes (adjusted where both ends have one); the basket is the equal-weight "
    "mean of the study's listings over the same window, mixing their currencies.",
)
NOTE = "Small samples: read the hit rates with their intervals and the base rates, and see the limits in the report."


@dataclass(frozen=True)
class Outcome:
    horizon: str
    state: str
    """scored; pending: the window is not over; unpriced: over, but the stored bars cannot price it."""
    ret: float | None
    basket: float | None
    """None when fewer than half the study's listings are priced for the window."""
    basket_n: int
    vs_basket: float | None


@dataclass(frozen=True)
class CellScore:
    id: int
    symbol: str
    as_of: date
    state: str
    verdict: str | None
    confidence: float | None
    run_id: int | None
    outcomes: tuple[Outcome, ...]
    """One per horizon, in HORIZONS order."""


@dataclass(frozen=True)
class Group:
    name: str
    n: int
    hit_rate: float | None
    """Buys that rose and sells that fell; None for holds, which call no direction."""
    hit_ci: tuple[float, float] | None
    """Wilson 95%, from MIN_N calls."""
    hit_rate_vs_basket: float | None
    hit_vs_basket_ci: tuple[float, float] | None
    mean_return: float | None
    """For a confidence band, as called: a sell's sign is flipped, so positive is right."""
    mean_vs_basket: float | None
    mean_confidence: float | None


@dataclass(frozen=True)
class HorizonScore:
    horizon: str
    months: int
    scored: int
    pending: int
    unpriced: int
    hit_rate: float | None
    """Over buys and sells."""
    hit_ci: tuple[float, float] | None
    hit_rate_vs_basket: float | None
    hit_vs_basket_ci: tuple[float, float] | None
    base_rate_up: float | None
    """The share of every priced (listing, day) window of the study that rose, verdict or not."""
    base_rate_beat_basket: float | None
    by_verdict: tuple[Group, ...]
    by_band: tuple[Group, ...]
    """Buys and sells only."""


@dataclass(frozen=True)
class Mix:
    buy: int
    hold: int
    sell: int
    buy_share: float | None
    mean_confidence: float | None


@dataclass(frozen=True)
class InterimVerdict:
    n: int
    mean_return: float | None
    """Raw: a sell did well when this is negative."""
    mean_as_called: float | None
    """Positive is right: a sell's sign flipped; None for holds."""


@dataclass(frozen=True)
class InterimHorizon:
    scored: int
    pending: int
    unpriced: int
    hit_rate: float | None
    hit_rate_ci: tuple[float, float] | None
    hit_rate_vs_basket: float | None
    hit_rate_vs_basket_ci: tuple[float, float] | None
    base_rate_up: float | None
    base_rate_beat_basket: float | None
    by_verdict: dict[str, InterimVerdict]


@dataclass(frozen=True)
class Interim:
    """The status's score block: small and stable, for a headless daily check to quote."""
    verdicts: int
    no_verdict: int
    buy_share: float | None
    mean_confidence: float | None
    prices_updated_at: datetime | None
    horizons: dict[str, InterimHorizon]
    note: str = NOTE


@dataclass(frozen=True)
class Score:
    study_id: int
    first_day: date
    last_day: date
    today: date
    total: int
    to_run: int
    """Cells queued or running."""
    failed: int
    no_verdict: int
    """Replays that finished without a verdict."""
    verdicts: int
    mix: Mix
    horizons: tuple[HorizonScore, ...]
    unpriced_listings: tuple[str, ...]
    """Listings with a closed window their stored bars do not reach: Update prices refetches them."""
    prices_updated_at: datetime | None
    cells: tuple[CellScore, ...]
    limits: tuple[str, ...] = LIMITS

    def interim(self) -> Interim:
        def verdict(g: Group) -> InterimVerdict:
            sign = _SIGN[g.name]
            return InterimVerdict(g.n, g.mean_return, sign * g.mean_return if sign and g.mean_return is not None
                                  else None)

        return Interim(self.verdicts, self.no_verdict, self.mix.buy_share, self.mix.mean_confidence,
                       self.prices_updated_at, {
                           h.horizon: InterimHorizon(h.scored, h.pending, h.unpriced, h.hit_rate, h.hit_ci,
                                                     h.hit_rate_vs_basket, h.hit_vs_basket_ci, h.base_rate_up,
                                                     h.base_rate_beat_basket,
                                                     {g.name: verdict(g) for g in h.by_verdict})
                           for h in self.horizons})

    def to_json(self) -> dict:
        return json.loads(json.dumps(asdict(self), default=_iso))


def _iso(value: date | datetime) -> str:
    return value.isoformat()


def wilson(hits: int, n: int) -> tuple[float, float] | None:
    """The Wilson score 95% interval of a hit rate; None under MIN_N."""
    if n < MIN_N:
        return None
    p, z2 = hits / n, Z95 * Z95
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def replay_report(ctx: AppContext) -> Score | None:
    """The current study scored in full, from stored bars."""
    study = ReplayRepo(ctx.conn).current()
    return score_study(ctx, study) if study else None


async def update_study_prices(ctx: AppContext) -> tuple[ListingOutcome, ...]:
    """Refetch, through the normal price update, the whole study window of each listing whose
    bars stop short of a closed window, so its closes share one split and dividend basis."""
    study = ReplayRepo(ctx.conn).current()
    if study is None:
        raise InvalidQuery("no replay study yet: create one first")
    score = score_study(ctx, study)
    since = min((c.as_of for c in score.cells), default=study.first_day) - STALE
    out = await update_listings(ctx, score.unpriced_listings, since) if score.unpriced_listings else []
    MetaRepo(ctx.conn).set(PRICES_AT, to_utc_iso(ctx.clock.utcnow()))
    return tuple(out)


def score_study(ctx: AppContext, study: StudyRow) -> Score:
    today = ctx.clock.utcnow().date()
    cells = ReplayRepo(ctx.conn).cells(study.id)
    symbols = [p["symbol"] for p in json.loads(study.listings)]
    days = sorted({c.as_of for c in cells})
    prices = _Prices(ctx, symbols, days[0] - STALE if days else study.first_day)
    moves = {(s, d, h): prices.forward(s, d, months, today) for s in symbols for d in days for h, months in HORIZONS}
    baskets = {(d, h): _basket([m.ret for s in symbols if (m := moves[(s, d, h)]).ret is not None], len(symbols))
               for d in days for h, _ in HORIZONS}
    windows = {(s, d, h): _outcome(moves[(s, d, h)], h, baskets[(d, h)])
               for s in symbols for d in days for h, _ in HORIZONS}
    calls = ResearchRepo(ctx.conn).verdicts([c.run_id for c in cells if c.state == DONE and c.run_id is not None])
    scored = tuple(_cell(c, calls.get(c.run_id) if c.state == DONE else None, windows) for c in cells)
    judged = [c for c in scored if c.verdict]
    at = MetaRepo(ctx.conn).get(PRICES_AT)
    return Score(
        study.id, study.first_day, study.last_day, today, len(cells),
        sum(1 for c in cells if c.state not in (DONE, FAILED)), sum(1 for c in cells if c.state == FAILED),
        sum(1 for c in cells if c.state == DONE) - len(judged), len(judged), _mix(judged),
        tuple(_horizon(judged, [o for (_, _, hh), o in windows.items() if hh == h], n, h, months)
              for n, (h, months) in enumerate(HORIZONS)),
        tuple(s for s in symbols if any(moves[(s, d, h)].fetch for d in days for h, _ in HORIZONS)),
        datetime.fromisoformat(at) if at else None, scored)


@dataclass(frozen=True)
class _Move:
    state: str
    ret: float | None = None
    fetch: bool = False
    """Unpriced because the bars stop short of the window's end: a refetch can price it."""


class _Prices:
    def __init__(self, ctx: AppContext, symbols: Sequence[str], start: date) -> None:
        repo = BarRepo(ctx.conn)
        self._bars: dict[str, list[Bar]] = {s: repo.since(s, start) for s in symbols}
        self._days = {s: [b.date for b in bars] for s, bars in self._bars.items()}

    def forward(self, symbol: str, as_of: date, months: int, today: date) -> _Move:
        """From the first close after the as-of day to the last close on or before the day
        `months` later: a replay reads that day's news to its end, often after the close."""
        bars, days = self._bars[symbol], self._days[symbol]
        target = _weekday_on_or_after(_months_after(as_of, months))
        if target >= today:
            return _Move(PENDING)
        if not days or days[-1] < target:
            return _Move(UNPRICED, fetch=True)
        i, j = bisect_right(days, as_of), bisect_right(days, target) - 1
        if i > j or days[i] - as_of > STALE or target - days[j] > STALE:
            return _Move(UNPRICED)
        low, high = SPLIT_RATIOS
        if any(not low <= bars[k + 1].close / bars[k].close <= high for k in range(i, j)):
            return _Move(UNPRICED)
        entry, exit_ = bars[i], bars[j]
        # Adjusted closes only as a pair: one end adjusted and the other not would be a made-up move.
        if entry.adj_close is not None and exit_.adj_close is not None:
            return _Move(SCORED, exit_.adj_close / entry.adj_close - 1)
        return _Move(SCORED, exit_.close / entry.close - 1)


def _basket(rets: Sequence[float], listings: int) -> tuple[float | None, int]:
    return (_mean(rets) if len(rets) * 2 >= listings else None), len(rets)


def _outcome(move: _Move, horizon: str, basket: tuple[float | None, int]) -> Outcome:
    mean, n = basket
    vs = move.ret - mean if move.ret is not None and mean is not None else None
    return Outcome(horizon, move.state, move.ret, mean if move.ret is not None else None, n, vs)


def _cell(c: CellRow, call: tuple[str, float] | None, windows: dict) -> CellScore:
    label, confidence = call if call and call[0] in VERDICTS else (None, None)
    return CellScore(c.id, c.symbol, c.as_of, c.state, label, confidence, c.run_id,
                     tuple(windows[(c.symbol, c.as_of, h)] for h, _ in HORIZONS))


def _mix(judged: Sequence[CellScore]) -> Mix:
    counts = {v: sum(1 for c in judged if c.verdict == v) for v in VERDICTS}
    return Mix(counts["buy"], counts["hold"], counts["sell"], counts["buy"] / len(judged) if judged else None,
               _mean([c.confidence for c in judged]))


def _horizon(judged: Sequence[CellScore], windows: Sequence[Outcome], n: int, name: str,
             months: int) -> HorizonScore:
    states = [c.outcomes[n].state for c in judged]
    done = [c for c in judged if c.outcomes[n].state == SCORED]
    calls = [c for c in done if _SIGN[c.verdict]]
    overall = _group("", calls, n, called=True)
    rets = [o.ret for o in windows if o.ret is not None]
    rels = [o.vs_basket for o in windows if o.vs_basket is not None]
    return HorizonScore(
        name, months, len(done), states.count(PENDING), states.count(UNPRICED), overall.hit_rate, overall.hit_ci,
        overall.hit_rate_vs_basket, overall.hit_vs_basket_ci, _share(rets), _share(rels),
        tuple(_group(v, [c for c in done if c.verdict == v], n, called=False) for v in VERDICTS),
        tuple(_group(band, [c for c in calls if _band(c.confidence) == band], n, called=True) for band in BANDS))


def _group(name: str, cells: Sequence[CellScore], n: int, called: bool) -> Group:
    pairs = [(_SIGN[c.verdict], c.outcomes[n]) for c in cells]

    def hits(pick) -> tuple[float | None, tuple[float, float] | None]:
        right = [sign * v > 0 for sign, o in pairs if sign and (v := pick(o)) is not None]
        return (_mean([float(r) for r in right]) if right else None), wilson(sum(right), len(right))

    def mean(pick) -> float | None:
        return _mean([(sign if called else 1) * v for sign, o in pairs if (v := pick(o)) is not None])

    hit, hit_ci = hits(lambda o: o.ret)
    rel, rel_ci = hits(lambda o: o.vs_basket)
    return Group(name, len(cells), hit, hit_ci, rel, rel_ci, mean(lambda o: o.ret), mean(lambda o: o.vs_basket),
                 _mean([c.confidence for c in cells]))


def _share(values: Sequence[float]) -> float | None:
    return _mean([1.0 if v > 0 else 0.0 for v in values])


def _band(confidence: float) -> str:
    return BANDS[bisect_right(BAND_EDGES, confidence)]


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _months_after(day: date, months: int) -> date:
    y, m = divmod(day.month - 1 + months, 12)
    year, month = day.year + y, m + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _weekday_on_or_after(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day
