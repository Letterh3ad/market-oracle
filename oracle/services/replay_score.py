"""How the replay study's verdicts did: each against its listing's price over the next one and
three months, and against the study's listings held in equal weight over the same months.
Scoring reads stored bars only, so the status poll can afford it; fetching the missing bars
is `update_study_prices`, run by the report command or the card's button."""

from __future__ import annotations

import calendar
import json
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from oracle.market.bars import Bar
from oracle.research.trace import VERDICTS
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, PriceUnavailable, UnknownSymbol
from oracle.services.prices_service import ListingOutcome, update_listing
from oracle.store.bars import BarRepo
from oracle.store.replay import DONE, FAILED, CellRow, ReplayRepo, StudyRow
from oracle.store.research import ResearchRepo

__all__ = ["LIMITS", "Score", "replay_report", "score_study", "update_study_prices"]

HORIZONS: tuple[tuple[str, int], ...] = (("1m", 1), ("3m", 3))
"""Label and months ahead."""
BAND_EDGES = (0.6, 0.8)
BANDS = ("under 0.60", "0.60 to 0.80", "0.80 and up")
STALE_ENTRY = timedelta(days=7)
"""An as-of close older than this is a gap in the bars, not the day's price."""
SCORED, PENDING, UNPRICED = "scored", "pending", "unpriced"
_SIGN = {"buy": 1, "hold": 0, "sell": -1}

LIMITS = (
    "Replays read filings and dated headlines only; GDELT covers about 3 months, so older dates lean on "
    "Google News.",
    "The listings and their fundamentals are today's (survivorship): companies that failed since are not "
    "in the universe.",
    "About 300 samples over about 15 dates detect a clear edge or a clear lack of one, not a small one.",
    "Interim results, before every replay has run and every window has closed, are noisier still.",
    "Returns are in each listing's currency from stored closes (adjusted where both ends have one); the "
    "basket is the equal-weight mean of the study's listings over the same window.",
)


@dataclass(frozen=True)
class Outcome:
    horizon: str
    state: str
    """scored; pending: the window is not over; unpriced: over, but no stored bars cover it."""
    ret: float | None
    basket: float | None
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
    hit_rate_vs_basket: float | None
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
    hit_rate_vs_basket: float | None
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
class InterimHorizon:
    scored: int
    pending: int
    unpriced: int
    hit_rate: float | None
    hit_rate_vs_basket: float | None
    mean_return: dict[str, float | None]
    """By verdict."""


@dataclass(frozen=True)
class Interim:
    """The status's score block: small and stable, for a headless daily check to quote."""
    verdicts: int
    no_verdict: int
    buy_share: float | None
    mean_confidence: float | None
    horizons: dict[str, InterimHorizon]


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
    """Listings with a closed window their stored bars do not cover: Update prices fetches them."""
    cells: tuple[CellScore, ...]
    limits: tuple[str, ...] = LIMITS

    def interim(self) -> Interim:
        return Interim(self.verdicts, self.no_verdict, self.mix.buy_share, self.mix.mean_confidence, {
            h.horizon: InterimHorizon(h.scored, h.pending, h.unpriced, h.hit_rate, h.hit_rate_vs_basket,
                                      {g.name: g.mean_return for g in h.by_verdict})
            for h in self.horizons})

    def to_json(self) -> dict:
        return json.loads(json.dumps(asdict(self), default=date.isoformat))


def replay_report(ctx: AppContext) -> Score | None:
    """The current study scored in full, from stored bars."""
    study = ReplayRepo(ctx.conn).current()
    return score_study(ctx, study) if study else None


async def update_study_prices(ctx: AppContext) -> tuple[ListingOutcome, ...]:
    """Fetch bars, through the normal price update, for each listing with a closed window its
    stored bars do not cover; one failing never stops the rest."""
    study = ReplayRepo(ctx.conn).current()
    if study is None:
        raise InvalidQuery("no replay study yet: create one first")
    out = []
    for symbol in score_study(ctx, study).unpriced_listings:
        try:
            r = await update_listing(ctx, symbol)
        except (PriceUnavailable, UnknownSymbol) as exc:
            out.append(ListingOutcome(symbol, 0, None, str(exc)))
        else:
            out.append(ListingOutcome(r.symbol, r.added, r.source, None))
    return tuple(out)


def score_study(ctx: AppContext, study: StudyRow) -> Score:
    today = ctx.clock.utcnow().date()
    cells = ReplayRepo(ctx.conn).cells(study.id)
    symbols = [p["symbol"] for p in json.loads(study.listings)]
    days = sorted({c.as_of for c in cells})
    prices = _Prices(ctx, symbols, days[0] - STALE_ENTRY if days else study.first_day)
    moves = {(s, d, h): prices.forward(s, d, months, today) for s in symbols for d in days for h, months in HORIZONS}
    baskets = {(d, h): _mean([m for s in symbols if (m := moves[(s, d, h)][1]) is not None])
               for d in days for h, _ in HORIZONS}
    calls = ResearchRepo(ctx.conn).verdicts([c.run_id for c in cells if c.state == DONE and c.run_id is not None])
    scored = tuple(_cell(c, calls.get(c.run_id) if c.state == DONE else None, moves, baskets) for c in cells)
    judged = [c for c in scored if c.verdict]
    return Score(
        study.id, study.first_day, study.last_day, today, len(cells),
        sum(1 for c in cells if c.state not in (DONE, FAILED)), sum(1 for c in cells if c.state == FAILED),
        sum(1 for c in cells if c.state == DONE) - len(judged), len(judged), _mix(judged),
        tuple(_horizon(judged, n, h, months) for n, (h, months) in enumerate(HORIZONS)),
        tuple(s for s in symbols if any(moves[(s, d, h)][0] == UNPRICED for d in days for h, _ in HORIZONS)),
        scored)


class _Prices:
    def __init__(self, ctx: AppContext, symbols: Sequence[str], start: date) -> None:
        repo = BarRepo(ctx.conn)
        self._bars: dict[str, list[Bar]] = {s: repo.since(s, start) for s in symbols}
        self._days = {s: [b.date for b in bars] for s, bars in self._bars.items()}

    def forward(self, symbol: str, as_of: date, months: int, today: date) -> tuple[str, float | None]:
        """The return from the as-of close to the close `months` later."""
        bars, days = self._bars[symbol], self._days[symbol]
        target = _weekday_on_or_after(_months_after(as_of, months))
        if not days or days[-1] < target:
            return (PENDING if target >= today else UNPRICED), None
        i = bisect_right(days, as_of) - 1
        if i < 0 or as_of - days[i] > STALE_ENTRY:
            return UNPRICED, None
        entry, exit_ = bars[i], bars[bisect_right(days, target) - 1]
        # Adjusted closes only as a pair: one end adjusted and the other not would be a made-up move.
        if entry.adj_close is not None and exit_.adj_close is not None:
            return SCORED, exit_.adj_close / entry.adj_close - 1
        return SCORED, exit_.close / entry.close - 1


def _cell(c: CellRow, call: tuple[str, float] | None, moves: dict, baskets: dict) -> CellScore:
    outcomes = []
    for h, _ in HORIZONS:
        state, ret = moves[(c.symbol, c.as_of, h)]
        basket = baskets[(c.as_of, h)] if ret is not None else None
        outcomes.append(Outcome(h, state, ret, basket, ret - basket if basket is not None else None))
    label, confidence = call if call and call[0] in VERDICTS else (None, None)
    return CellScore(c.id, c.symbol, c.as_of, c.state, label, confidence, c.run_id, tuple(outcomes))


def _mix(judged: Sequence[CellScore]) -> Mix:
    counts = {v: sum(1 for c in judged if c.verdict == v) for v in VERDICTS}
    return Mix(counts["buy"], counts["hold"], counts["sell"], counts["buy"] / len(judged) if judged else None,
               _mean([c.confidence for c in judged]))


def _horizon(judged: Sequence[CellScore], n: int, name: str, months: int) -> HorizonScore:
    states = [c.outcomes[n].state for c in judged]
    done = [c for c in judged if c.outcomes[n].state == SCORED]
    calls = [c for c in done if _SIGN[c.verdict]]
    overall = _group("", calls, n, called=True)
    return HorizonScore(
        name, months, len(done), states.count(PENDING), states.count(UNPRICED), overall.hit_rate,
        overall.hit_rate_vs_basket,
        tuple(_group(v, [c for c in done if c.verdict == v], n, called=False) for v in VERDICTS),
        tuple(_group(band, [c for c in calls if _band(c.confidence) == band], n, called=True) for band in BANDS))


def _group(name: str, cells: Sequence[CellScore], n: int, called: bool) -> Group:
    signs = [_SIGN[c.verdict] for c in cells]
    rets = [c.outcomes[n].ret for c in cells]
    rels = [c.outcomes[n].vs_basket for c in cells]
    directional = [i for i, s in enumerate(signs) if s]

    def hits(values):
        return _mean([1.0 if signs[i] * values[i] > 0 else 0.0 for i in directional]) if directional else None

    def mean(values):
        return _mean([(signs[i] if called else 1) * v for i, v in enumerate(values)])

    return Group(name, len(cells), hits(rets), hits(rels), mean(rets), mean(rels),
                 _mean([c.confidence for c in cells]))


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
