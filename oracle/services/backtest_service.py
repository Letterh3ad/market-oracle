"""A backtest: the strategy's fixed rules replayed over a watchlist's stored history, in
memory, against an equal-weight buy-and-hold, the momentum baseline and the regime lines. It only reads: the paper
accounts, leans, signals and decision log never change. AI verdicts are never backtested."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime

from oracle.analysts.indicators import YEAR
from oracle.broker.backtest import Trade, replay
from oracle.broker.baseline import ceiling
from oracle.broker.compare import buy_and_hold, max_fall, returns_since
from oracle.broker.momentum import caption as momentum_caption
from oracle.broker.momentum import momentum_lean
from oracle.broker.orders import Side
from oracle.broker.regime import ROTATION
from oracle.broker.walk_forward import MONTHS, Figures, WalkLine, judge, segments
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, NoPrices, UnknownBacktest, UnknownSymbol
from oracle.services.paper_service import Line, strategy_rules
from oracle.services.prices_service import ConvertedBar, price_history_in_base
from oracle.services.regime_lines import LINE_LABELS, REGIME_LINE, REGIME_V2_LINE, REGIME_WF_LINE, regime_lines
from oracle.services.watch_service import watchlist_symbols
from oracle.store.backtests import BacktestRepo
from oracle.store.bars import BarRepo

__all__ = ["KEEP", "Backtest", "Figures", "KeptBacktest", "Line", "Trade", "WalkForward", "check_backtest",
           "keep_backtest", "kept_backtest", "kept_backtests", "run_backtest", "stored_span"]

BPS = 10_000
KEEP = 10
"""Results kept for comparing runs; older ones are dropped."""


@dataclass(frozen=True)
class WalkForward:
    months: int
    segments: tuple[tuple[date, date], ...]
    """Back to back from the backtest's first day; each line starts afresh in each."""
    lines: tuple[WalkLine, ...]
    """In the backtest's line order, each judged against buy and hold."""
    fits: tuple[tuple[date, dict[str, dict[str, float]]], ...]
    """regime_wf's presets per segment: (market day they took effect, mood -> method -> weight)."""


@dataclass(frozen=True)
class Backtest:
    watchlist: str
    start: date
    end: date
    slippage_bps: float
    commission: float
    """Per trade, base currency."""
    listings: tuple[str, ...]
    """The list's listings with prices in the range; the rest are named in the notes."""
    lines: tuple[Line, ...]
    """The strategy, buy and hold, the momentum baseline, then the regime lines (runs kept before
    any of them have fewer), each as its return since the first day."""
    equity: tuple[tuple[date, float], ...]
    """The strategy's cash plus positions at each day's close, base currency."""
    trades: tuple[Trade, ...]
    notes: tuple[str, ...]
    walk_forward: WalkForward | None = None
    """Only when asked for; runs kept before it have none."""


def stored_span(ctx: AppContext, watchlist: str) -> tuple[str, date | None, date | None]:
    """The list's stored name and the first and last day any of its listings has a price."""
    name, symbols = watchlist_symbols(ctx, watchlist)
    span = BarRepo(ctx.conn).span(symbols)
    return (name, *span) if span else (name, None, None)


def check_backtest(ctx: AppContext, watchlist: str, start: date, end: date, slippage_bps: float,
                   commission: float, walk_forward: bool = False) -> str:
    """The list's stored name, or why the request cannot run; cheap, so a job is refused
    before it is queued."""
    if start >= end:
        raise InvalidQuery(f"the first day ({start.isoformat()}) must be before the last ({end.isoformat()})")
    if not (math.isfinite(slippage_bps) and slippage_bps >= 0):
        raise InvalidQuery(f"slippage must be zero or more basis points, not {slippage_bps}")
    if not (math.isfinite(commission) and commission >= 0):
        raise InvalidQuery(f"commission must be zero or more, not {commission}")
    if walk_forward and not segments(start, end, MONTHS):
        raise InvalidQuery(f"a walk-forward needs at least {MONTHS // 2} months from the first day to the last")
    name, first, last = stored_span(ctx, watchlist)
    if first is None:
        raise NoPrices(f"no stored prices for the {name} list: update its prices first")
    if start < first or end > last:
        raise InvalidQuery(f"prices for the {name} list are stored from {first.isoformat()} to {last.isoformat()}: "
                           "pick days inside that")
    return name


def run_backtest(ctx: AppContext, watchlist: str, start: date, end: date, slippage_bps: float = 10.0,
                 commission: float = 0.0, walk_forward: bool = False) -> Backtest:
    """Replay the strategy's rules day by day from `start` to `end` over the list's stored
    bars, each day seeing only bars up to its own close. With `walk_forward`, also every line
    over back-to-back segments, each from a fresh start, and the regime_wf line refitted at
    each segment's start."""
    name = check_backtest(ctx, watchlist, start, end, slippage_bps, commission, walk_forward)
    symbols = watchlist_symbols(ctx, name)[1]
    history, gone, no_prices = {}, [], []
    for symbol in symbols:
        try:
            rows = [c for c in price_history_in_base(ctx, symbol) if c.bar.date <= end]
        except UnknownSymbol:
            gone.append(symbol)
            continue
        if any(c.bar.date >= start for c in rows):
            history[symbol] = rows
        else:
            no_prices.append(symbol)
    if not history:
        raise NoPrices(f"no stored prices for the {name} list from {start.isoformat()} to {end.isoformat()}")
    rules, capital, base = strategy_rules(ctx), ctx.config.risk.account, ctx.config.base_currency
    top = rules.max_positions

    spans = segments(start, end, MONTHS) if walk_forward else []

    def run(lean=None, cap=None, first=start, last=end):
        return replay(history, first, last, rules=rules, risk=asdict(ctx.config.risk), slippage=slippage_bps / BPS,
                      commission=commission, lean=lean, cap=cap)

    def traded(name: str, caption: str, x, label: str = "") -> Line:
        return Line(name, caption, tuple(returns_since(x.equity, start)), max_fall(x.equity), len(x.trades),
                    sum(t.costs for t in x.trades), label)

    momentum_leans = momentum_lean({s: [c.bar for c in rows] for s, rows in history.items()}, top)
    r = run()
    strategy = traded("strategy", "Strategy: the fixed rules on technicals-only leans.", r)
    m = run(momentum_leans)
    momentum = traded("momentum", momentum_caption(rules), m, "Momentum")
    blends = regime_lines(ctx, start, end, rules, history, refits=[a for a, _ in spans])
    runs = {name: run(x.book.lean_source(), x.book.cap) for name, x in blends.items()}
    regimes = tuple(traded(name, blends[name].caption, x, LINE_LABELS[name]) for name, x in runs.items())
    closes = {s: [(c.bar.date, c.close_base) for c in rows] for s, rows in history.items()}
    held, no_rate = buy_and_hold(closes, start, capital)
    n = len(history) - len(no_rate)
    held_line = Line("buy_and_hold", f"Buy and hold: equal amounts of the {n} listing{'' if n == 1 else 's'}, bought "
                                     f"at the first close from {start.isoformat()} and never sold, with no costs.",
                     tuple(returns_since(held, start)) if held else (), max_fall(held), 0, 0.0)
    notes = _notes(ctx, name, start, end, slippage_bps, commission, rules.min_probability, history)
    notes += [f"Left out, no longer in the universe: {', '.join(gone)}."] if gone else []
    notes += [f"Left out, no stored prices from {start.isoformat()} to {end.isoformat()}: {', '.join(no_prices)}."
              ] if no_prices else []
    notes += [f"Left out of buy and hold, no {base} exchange rate from {start.isoformat()} on: {', '.join(no_rate)}."
              ] if no_rate else []
    for x, could, still in ((r, "Could", "Still"), (m, "Momentum could", "Momentum still"),
                            *((runs[n], f"{LINE_LABELS[n]} could", f"{LINE_LABELS[n]} still") for n in runs)):
        notes += [f"{could} not fill: {note}." for note in x.rejected]
        notes += [f"{still} waiting to fill on the last day: {', '.join(x.unfilled)}."] if x.unfilled else []
    notes.append(rotation_note(runs[REGIME_V2_LINE].trades, runs[REGIME_LINE].trades))
    walked = None
    if spans:
        sources = {"strategy": (None, None), "buy_and_hold": None, "momentum": (momentum_leans, None),
                   **{n: (x.book.lean_source(), x.book.cap) for n, x in blends.items()}}
        equities = {n: [_held(closes, a, b, capital) if source is None else run(*source, first=a, last=b).equity
                        for a, b in spans] for n, source in sources.items()}
        fits = tuple((day, {m: dict(w) for m, w in presets.items()})
                     for day, presets in blends[REGIME_WF_LINE].book.fits)
        walked = WalkForward(MONTHS, tuple(spans), judge(equities, "buy_and_hold"), fits)
        notes.append(_walk_note(spans, end, capital, base))
    return Backtest(name, start, end, slippage_bps, commission, tuple(history), (strategy, held_line, momentum, *regimes),
                    r.equity, r.trades, tuple(notes), walked)


def _held(closes: dict[str, list[tuple[date, float | None]]], first: date, last: date,
          capital: float) -> list[tuple[date, float]]:
    return buy_and_hold({s: [(d, c) for d, c in rows if d <= last] for s, rows in closes.items()}, first, capital)[0]


def _walk_note(spans: list[tuple[date, date]], end: date, capital: float, base: str) -> str:
    left = (f" The days after {spans[-1][1].isoformat()} to {end.isoformat()} are under half a segment and are left "
            "out of it." if spans[-1][1] < end else "")
    return (f"Walk-forward: {len(spans)} back-to-back segments of {MONTHS} months from {spans[0][0].isoformat()}. "
            f"Each is a fresh start on the same {capital:,.0f} {base}, nothing held, so every segment is judged on "
            "its own and a good early stretch never carries a later one; buy and hold starts afresh too. Sharpe is "
            "from daily equity with a zero rate, annualised over 252 days; return over max fall divides a return by "
            "its largest fall. Regime-wf refits its mood weights at each segment's first market day from pick "
            "windows closed before it, so its first fits read less than two years when less is stored." + left)


def rotation_note(v2: tuple[Trade, ...], v1: tuple[Trade, ...]) -> str:
    """How much of regime-v2's turnover its rotation made, beside the regime line's."""
    rotated = sum(1 for t in v2 if t.reason.startswith(ROTATION))
    return (f"Regime-v2 rotated {rotated} holding{'' if rotated == 1 else 's'} out for a stronger buy, in "
            f"{len(v2)} trades against the regime line's {len(v1)}.")


@dataclass(frozen=True)
class KeptBacktest:
    id: int
    ran_at: datetime
    base_currency: str
    """The base currency when it ran: its amounts stay in it if the setting changes."""
    backtest: Backtest


def keep_backtest(ctx: AppContext, b: Backtest) -> KeptBacktest:
    """Store the result beside the last few, so runs can be compared later."""
    ran_at, base = ctx.clock.utcnow(), ctx.config.base_currency
    record = {"ran_at": ran_at.isoformat(), "base_currency": base, "backtest": asdict(b)}
    run_id = BacktestRepo(ctx.conn).add(json.dumps(record, default=_iso), KEEP)
    return KeptBacktest(run_id, ran_at, base, b)


def kept_backtests(ctx: AppContext) -> list[KeptBacktest]:
    """Newest first."""
    return [_kept(run_id, value) for run_id, value in BacktestRepo(ctx.conn).recent()]


def kept_backtest(ctx: AppContext, run_id: int) -> KeptBacktest:
    value = BacktestRepo(ctx.conn).get(run_id)
    if value is None:
        raise UnknownBacktest(f"no kept backtest {run_id}: only the last {KEEP} are kept")
    return _kept(run_id, value)


def _iso(value: date) -> str:
    return value.isoformat()


def _kept(run_id: int, value: str) -> KeptBacktest:
    record = json.loads(value)
    b = record["backtest"]
    day = date.fromisoformat
    lines = tuple(Line(**{**line, "points": tuple((day(d), r) for d, r in line["points"])}) for line in b["lines"])
    trades = tuple(Trade(**{**t, "day": day(t["day"]), "side": Side(t["side"])}) for t in b["trades"])
    wf = b.get("walk_forward")
    walked = None if wf is None else WalkForward(
        wf["months"], tuple((day(a), day(z)) for a, z in wf["segments"]),
        tuple(WalkLine(x["name"], tuple(Figures(**f) for f in x["segments"]), Figures(**x["overall"]), x["beat"])
              for x in wf["lines"]),
        tuple((day(d), presets) for d, presets in wf["fits"]))
    backtest = Backtest(
        b["watchlist"], day(b["start"]), day(b["end"]), b["slippage_bps"], b["commission"], tuple(b["listings"]),
        lines, tuple((day(d), v) for d, v in b["equity"]), trades, tuple(b["notes"]), walked)
    return KeptBacktest(run_id, datetime.fromisoformat(record["ran_at"]), record["base_currency"], backtest)


def _notes(ctx: AppContext, name: str, start: date, end: date, slippage_bps: float, commission: float,
           threshold: float, history: dict[str, list[ConvertedBar]]) -> list[str]:
    base, cap = ctx.config.base_currency, ceiling(1)
    fundamentals = (f"The strategy line leaves out value, quality and growth: only today's fundamentals are stored, "
                    f"so using them on past days would leak the future. Each of its leans is the technicals baseline "
                    f"alone, so its probability is at most {cap:.1%}. The regime line reads stored value and quality "
                    f"signals only from the day each was recorded (its caption says which it had).")
    if threshold > cap:
        fundamentals += f" That is under the {threshold:.0%} threshold, so the strategy cannot buy anything."
    notes = [
        f"The {name} list is today's: companies that were delisted, taken over or went bust before today are "
        "missing from it, so every line likely looks better than investing back then would have (survivorship "
        "bias). It reaches the regime line's mood too, which reads today's stored listings back in time.",
        fundamentals,
        f"Each day's decisions see only prices up to that day's close. Orders fill at the next trading day's open, "
        f"{slippage_bps:g} basis points worse, at that day's exchange rate, plus {commission:,.2f} {base} "
        "commission per trade. No FX spread, stamp duty or exchange fees.",
        "Only fixed rules are replayed; AI verdicts are never backtested.",
        "Momentum trades under the strategy's rules and costs. Its ranks read stored closes, which a split between "
        "price updates can leave on two bases: a listing with a move outside 0.5x to 2x in the year before a month's "
        "end is not ranked that month (a full price refetch fixes it).",
    ]
    short = [s for s, rows in history.items() if sum(1 for c in rows if c.bar.date < start) <= YEAR]
    if short:
        notes.append(f"Under a year of prices before {start.isoformat()}, so their leans start late or lack the "
                     f"trend: {', '.join(short)}.")
    return notes
