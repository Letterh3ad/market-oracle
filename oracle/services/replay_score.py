"""How the replay study's verdicts did: each against its listing's price over the next one and
three months, and against the study's listings held in equal weight over the same months.
Scoring reads stored bars only, so the status poll can afford it; fetching the missing bars
is `update_study_prices`, run by the report command, the card's button and the tick. The full
report adds the verdicts traded as an account (`replay_account`), which the poll leaves out."""

from __future__ import annotations

import calendar
import json
import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta

from oracle.market.bars import Bar
from oracle.research.trace import VERDICTS
from oracle.services.context import AppContext
from oracle.services.cutoff_service import replay_cutoff
from oracle.services.errors import InvalidQuery
from oracle.services.prices_service import ListingOutcome, update_listings
from oracle.services.replay_account import Account, simulate
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.meta import MetaRepo
from oracle.store.replay import DONE, FAILED, CellRow, ReplayRepo, StudyRow
from oracle.store.research import ResearchRepo

__all__ = ["LIMITS", "Account", "Score", "has_study", "replay_report", "score_study", "update_study_prices", "wilson"]

HORIZONS: tuple[tuple[str, int], ...] = (("1m", 1), ("3m", 3))
"""Label and months ahead."""
BAND_EDGES = (0.6, 0.8)
BANDS = ("under 0.60", "0.60 to 0.80", "0.80 and up")
STALE = timedelta(days=7)
"""A close further than this from the day it stands for is a gap in the bars, not that day's price."""
SPLIT_RATIOS = (0.5, 2.0)
"""A day-over-day move outside these is taken for a split the stored bars were not re-based for."""
SIZE_HISTORY = timedelta(days=400)
"""Bars before the study the account's risk sizes read: a year of trading days, with room."""
MIN_N = 10
"""Fewer hits and misses than this get no interval: it would span most of 0 to 1."""
Z95 = 1.96
SCORED, PENDING, UNPRICED = "scored", "pending", "unpriced"
PRICES_KEY = "replay_prices"
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
    """The share of the called windows (the buys and sells scored) that rose, whatever the call."""
    base_rate_beat_basket: float | None
    expected_hit_rate: float | None
    """What calls with no skill would hit, given the base rate and this mix of buys and sells."""
    expected_hit_rate_vs_basket: float | None
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
    expected_hit_rate: float | None
    expected_hit_rate_vs_basket: float | None
    by_verdict: dict[str, InterimVerdict]


@dataclass(frozen=True)
class Interim:
    """The status's score block: small and stable, for a headless daily check to quote."""
    verdicts: int
    no_verdict: int
    buy_share: float | None
    mean_confidence: float | None
    prices_checked_at: datetime | None
    price_failures: tuple[str, ...]
    horizons: dict[str, InterimHorizon]
    excluded: int = 0
    """Replays left out: on or before the current cutoff."""
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
    refetch_listings: tuple[str, ...]
    """Listings with a window closed on or after their last whole-window fetch: the next price
    update refetches them."""
    prices_checked_at: datetime | None
    """When study prices were last updated or found current."""
    price_failures: tuple[str, ...]
    """Listings that update could not fetch."""
    cells: tuple[CellScore, ...]
    excluded: int = 0
    """Replays done on or before the current cutoff (a later probe or claim moved it), left out:
    the model may have known what came next."""
    limits: tuple[str, ...] = LIMITS
    account: Account | None = None
    """The verdicts traded as an account; only in the full report (`replay_report`), None
    before any listing is priced."""

    def interim(self) -> Interim:
        def verdict(g: Group) -> InterimVerdict:
            sign = _SIGN[g.name]
            return InterimVerdict(g.n, g.mean_return, sign * g.mean_return if sign and g.mean_return is not None
                                  else None)

        return Interim(self.verdicts, self.no_verdict, self.mix.buy_share, self.mix.mean_confidence,
                       self.prices_checked_at, self.price_failures, {
                           h.horizon: InterimHorizon(h.scored, h.pending, h.unpriced, h.hit_rate, h.hit_ci,
                                                     h.hit_rate_vs_basket, h.hit_vs_basket_ci, h.base_rate_up,
                                                     h.base_rate_beat_basket, h.expected_hit_rate,
                                                     h.expected_hit_rate_vs_basket,
                                                     {g.name: verdict(g) for g in h.by_verdict})
                           for h in self.horizons}, self.excluded)

    def to_json(self) -> dict:
        out = json.loads(json.dumps(asdict(self), default=_iso))
        if self.account:
            for shaped, line in zip(out["account"]["lines"], self.account.lines):
                shaped["return"] = line.return_
        return out


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


def has_study(ctx: AppContext) -> bool:
    return ReplayRepo(ctx.conn).current() is not None


def replay_report(ctx: AppContext) -> Score | None:
    """The current study scored in full, with its account, from stored bars."""
    study = ReplayRepo(ctx.conn).current()
    if study is None:
        return None
    score = score_study(ctx, study)
    return replace(score, account=_account(ctx, study, score.cells))


def _account(ctx: AppContext, study: StudyRow, cells: Sequence[CellScore]) -> Account | None:
    # Days the model may know the outcome of are left out whole, so neither line starts there.
    cutoff = replay_cutoff(ctx).cutoff
    cells = [c for c in cells if not (cutoff and c.as_of <= cutoff)]
    first: dict[str, date] = {}
    for c in cells:
        first[c.symbol] = min(first.get(c.symbol, c.as_of), c.as_of)
    if not first:
        return None
    start, repo = min(first.values()), BarRepo(ctx.conn)
    fetched = {s: date.fromisoformat(d) for s, d in _price_state(ctx, study.id).get("fetched", {}).items()}
    history, left_out = {}, []
    for p in json.loads(study.listings):
        s = p["symbol"]
        if s not in first:
            continue
        bars = repo.since(s, start - SIZE_HISTORY)
        why = _unpriced(bars, first[s], fetched.get(s))
        if why:
            left_out.append(f"{s}: {why}")
        else:
            history[s] = _sizing_from(bars, first[s])
    end = max((b[-1].date for b in history.values()), default=None)
    if end is None or end <= start:
        return None
    calls: dict[str, list] = {}
    for c in sorted(cells, key=lambda c: c.as_of):
        if c.verdict and c.symbol in history:
            calls.setdefault(c.symbol, []).append((c.as_of, c.verdict, c.confidence))
    lagging = {s: b[-1].date for s, b in history.items() if end - b[-1].date > STALE}
    return simulate(ctx, history, {s: first[s] for s in history}, calls, start, end, left_out, lagging)


def _unpriced(bars: Sequence[Bar], first: date, fetched: date | None) -> str | None:
    """Why the account cannot hold the listing from the first close after its first as-of
    day, by the rules a scored window is priced by; None when it can. A listing whose bars
    only lag is held flat after its last close; one a whole fetch found nothing newer for
    has stopped trading."""
    span = [b for b in bars if b.date > first]
    if not span or span[0].date - first > STALE:
        return "no close within 7 days after its first as-of day"
    if fetched is not None and fetched - span[-1].date > STALE:
        return f"no close since {span[-1].date.isoformat()}"
    if not all(_price(b.close) for b in span):
        return "a zero or missing close"
    if not _steady(span):
        return "a day-over-day move outside 0.5x to 2x, taken for a split the stored bars were not re-based for"
    return None


def _steady(bars: Sequence[Bar]) -> bool:
    low, high = SPLIT_RATIOS
    return all(low <= b.close / a.close <= high for a, b in zip(bars, bars[1:]))


def _sizing_from(bars: Sequence[Bar], first: date) -> list[Bar]:
    """The bars after the last zero close or suspect split before the study, so neither
    reaches a risk size (a split between fetches would read as a huge swing)."""
    cut = 0
    for i, (a, b) in enumerate(zip(bars, bars[1:])):
        if b.date > first:
            break
        if not (_price(a.close) and _price(b.close) and _steady((a, b))):
            cut = i + 1
    return [b for b in bars[cut:] if _price(b.close)]


async def update_study_prices(ctx: AppContext) -> tuple[ListingOutcome, ...]:
    """Refetch, through the normal price update, the whole study window and the year before
    it of each listing due one (see `Score.refetch_listings`), so its closes share one split
    and dividend basis."""
    study = ReplayRepo(ctx.conn).current()
    if study is None:
        raise InvalidQuery("no replay study yet: create one first")
    score = score_study(ctx, study)
    # From a year and more before, so the account's risk sizes read closes on the same basis.
    since = min((c.as_of for c in score.cells), default=study.first_day) - SIZE_HISTORY
    out = await update_listings(ctx, score.refetch_listings, since) if score.refetch_listings else []
    state = _price_state(ctx, study.id)
    today = ctx.clock.utcnow().date().isoformat()
    _save_price_state(ctx, study.id, {
        "checked_at": to_utc_iso(ctx.clock.utcnow()), "failures": [o.symbol for o in out if o.error],
        "fetched": {**state.get("fetched", {}), **{o.symbol: today for o in out if not o.error and not o.skipped}}})
    return tuple(out)


def _price_state(ctx: AppContext, study_id: int) -> dict:
    """When each listing's whole window was last fetched, and the last check's time and failures."""
    raw = MetaRepo(ctx.conn).get(f"{PRICES_KEY}:{study_id}")
    return json.loads(raw) if raw else {}


def _save_price_state(ctx: AppContext, study_id: int, state: dict) -> None:
    MetaRepo(ctx.conn).set(f"{PRICES_KEY}:{study_id}", json.dumps(state))


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
    cutoff = replay_cutoff(ctx).cutoff
    counted = {c.id for c in cells if c.state == DONE and not (cutoff and c.as_of <= cutoff)}
    calls = ResearchRepo(ctx.conn).verdicts([c.run_id for c in cells if c.id in counted and c.run_id is not None])
    scored = tuple(_cell(c, calls.get(c.run_id) if c.id in counted else None, windows) for c in cells)
    judged = [c for c in scored if c.verdict]
    state = _price_state(ctx, study.id)
    fetched = {s: date.fromisoformat(d) for s, d in state.get("fetched", {}).items()}
    targets = sorted({_target(d, months) for d in days for _, months in HORIZONS})

    def due(symbol: str) -> bool:
        # A window that closed on or after the last whole-window fetch day reads bars that
        # incremental updates added, possibly on a newer basis: only a new whole-window fetch settles it.
        last = fetched.get(symbol)
        return any(t < today and (last is None or t >= last) for t in targets)

    return Score(
        study.id, study.first_day, study.last_day, today, len(cells),
        sum(1 for c in cells if c.state not in (DONE, FAILED)), sum(1 for c in cells if c.state == FAILED),
        len(counted) - len(judged), len(judged), _mix(judged),
        tuple(_horizon(judged, n, h, months) for n, (h, months) in enumerate(HORIZONS)),
        tuple(s for s in symbols if due(s)),
        datetime.fromisoformat(state["checked_at"]) if "checked_at" in state else None,
        tuple(state.get("failures", ())), scored,
        sum(1 for c in cells if c.state == DONE) - len(counted))


@dataclass(frozen=True)
class _Move:
    state: str
    ret: float | None = None


def _target(as_of: date, months: int) -> date:
    return _weekday_on_or_after(_months_after(as_of, months))


def _price(close: float | None) -> bool:
    return close is not None and close > 0


class _Prices:
    def __init__(self, ctx: AppContext, symbols: Sequence[str], start: date) -> None:
        repo = BarRepo(ctx.conn)
        self._bars: dict[str, list[Bar]] = {s: repo.since(s, start) for s in symbols}
        self._days = {s: [b.date for b in bars] for s, bars in self._bars.items()}

    def forward(self, symbol: str, as_of: date, months: int, today: date) -> _Move:
        """From the first close after the as-of day to the last close on or before the day
        `months` later: a replay reads that day's news to its end, often after the close."""
        bars, days = self._bars[symbol], self._days[symbol]
        target = _target(as_of, months)
        if target >= today:
            return _Move(PENDING)
        if not days or days[-1] < target:
            return _Move(UNPRICED)
        i, j = bisect_right(days, as_of), bisect_right(days, target) - 1
        if i > j or days[i] - as_of > STALE or target - days[j] > STALE:
            return _Move(UNPRICED)
        if not all(_price(bars[k].close) for k in range(i, j + 1)):
            return _Move(UNPRICED)
        low, high = SPLIT_RATIOS
        if any(not low <= bars[k + 1].close / bars[k].close <= high for k in range(i, j)):
            return _Move(UNPRICED)
        entry, exit_ = bars[i], bars[j]
        # Adjusted closes only as a pair: one end adjusted and the other not would be a made-up move.
        if _price(entry.adj_close) and _price(exit_.adj_close):
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


def _horizon(judged: Sequence[CellScore], n: int, name: str, months: int) -> HorizonScore:
    states = [c.outcomes[n].state for c in judged]
    done = [c for c in judged if c.outcomes[n].state == SCORED]
    calls = [c for c in done if _SIGN[c.verdict]]
    overall = _group("", calls, n, called=True)
    up, expected = _base(calls, n, lambda o: o.ret)
    beat, expected_vs = _base(calls, n, lambda o: o.vs_basket)
    return HorizonScore(
        name, months, len(done), states.count(PENDING), states.count(UNPRICED), overall.hit_rate, overall.hit_ci,
        overall.hit_rate_vs_basket, overall.hit_vs_basket_ci, up, beat, expected, expected_vs,
        tuple(_group(v, [c for c in done if c.verdict == v], n, called=False) for v in VERDICTS),
        tuple(_group(band, [c for c in calls if _band(c.confidence) == band], n, called=True) for band in BANDS))


def _base(calls: Sequence[CellScore], n: int, pick) -> tuple[float | None, float | None]:
    """How often the called windows went up, and the hit rate a caller with no skill would
    expect from that and this mix of buys and sells."""
    pairs = [(_SIGN[c.verdict], v) for c in calls if (v := pick(c.outcomes[n])) is not None]
    p = _share([v for _, v in pairs])
    if p is None:
        return None, None
    return p, _mean([p if sign > 0 else 1 - p for sign, _ in pairs])


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
