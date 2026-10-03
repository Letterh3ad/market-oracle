"""The research lab's strategy test (research-lab 02, spec 6 and 7): a registered rule-based
portfolio strategy backtested against index buy-and-hold on train (2010-2019, reported) and confirm
(2020 on, judged), after UK retail costs from the user's `[plan]`. PASS: beats the index after
costs on confirm and a deflated Sharpe probability of its daily excess over the index of at least
0.95 given the variants its registration says were tried. Prices come from the research database;
results are kept there beside the signal tests. Also reproduces the trend overlay (FAILed, never
judged again) through the same engine as a check on it."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date

from oracle.analysts.lab import DSR_BAR, deflated_sharpe, moments
from oracle.broker.projection import NO_FEES
from oracle.broker.strategy_backtest import Decide, StrategyRun, simulate
from oracle.broker.trend_overlay import Session, figures, month_end_decisions
from oracle.broker.uk_costs import STAMP_DUTY, Listing, UkCosts, listing_of
from oracle.services import trend_service
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, NoPrices
from oracle.services.fees_service import user_fees
from oracle.services.lab_registry import check_committed, require_committed
from oracle.services.signal_test_service import STRATEGY_PREFIX
from oracle.services.testbed_service import research_context
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.signal_tests import SignalTestRepo

__all__ = ["BENCHMARK", "CONFIRM_START", "KEY_PREFIX", "POT", "STRATEGIES", "TRAIN_END", "TRAIN_START", "CheckRow",
           "CostView", "Strategy", "StrategyLine", "StrategyTest", "StrategyWindow", "TrendCheck", "check_strategy",
           "cost_view", "keep_strategy_test", "run_strategy", "strategy_tests", "trend_check", "user_costs"]

KEY_PREFIX = STRATEGY_PREFIX
"""A kept result's name in the signal tests table: the prefix and the registration's key."""
TRAIN_START, TRAIN_END = date(2010, 1, 1), date(2019, 12, 31)
CONFIRM_START = date(2020, 1, 1)
BENCHMARK = "^SP500TR"
POT = 20_000.0
"""The pot a strategy test trades, GBP: one year's ISA allowance. Fixed so a dealing fee weighs the
same in every test."""
MATCH = 1e-4
"""How close a reproduced trend overlay figure must be to the kept one: 0.01 of a percentage point."""
INDEX_NOTE = ("The index is bought once at the first open and held as a GBP-traded fund (no stamp duty, no FX fee); "
              "it pays the same platform fee.")


@dataclass(frozen=True)
class Strategy:
    """A strategy test's code; its registration (same key) holds the rule in words and the bar."""
    name: str
    label: str
    symbols: tuple[str, ...]
    rule: Callable[[Mapping[str, Mapping[date, Session]]], Decide]
    """(adjusted sessions per symbol) -> the decision for each close, reading only that close and before."""
    benchmark: str = BENCHMARK
    bands: Mapping[str, str] = field(default_factory=dict)
    """Size band per symbol for its spread; none is the default spread."""


STRATEGIES: dict[str, Strategy] = {}
"""Strategy tests with code. Each runs only once its registration is committed."""


def check_strategy(key: str, registry: dict[str, Strategy] | None = None) -> Strategy:
    registry = STRATEGIES if registry is None else registry
    if key not in registry:
        have = ", ".join(sorted(registry)) or "none yet"
        raise InvalidQuery(f"no strategy code for {key!r}; strategies with code: {have}")
    return registry[key]


def user_costs(ctx: AppContext) -> UkCosts:
    return UkCosts(user_fees(ctx))


@dataclass(frozen=True)
class CostView:
    """The cost model a strategy test pays, for the page and the CLI."""
    platform: str
    percent_fee: float
    percent_cap: float
    flat_fee: float
    dealing_fee: float
    fx_fee: float
    stamp_duty: float
    half_spreads: tuple[tuple[str, float], ...]
    default_half_spread: float
    pot: float
    notes: tuple[str, ...]


def cost_view(ctx: AppContext) -> CostView:
    p, c = ctx.config.plan, user_costs(ctx)
    notes = ["Dealing per trade and FX on every trade of a non-GBP listing (both ways) come from your [plan] settings; "
             "the platform fee comes off the pot at each month end.",
             "Half the bid-ask spread is paid on each trade, by size band (the paper accounts' slippage table).",
             f"Stamp duty {STAMP_DUTY:.1%} on buys of UK shares only (UK-listed ETFs are exempt).",
             INDEX_NOTE]
    if not any((p.percent_fee, p.flat_fee, p.dealing_fee, p.fx_fee)):
        notes.append("No platform fees are set: enter your platform's prices in Settings, [plan].")
    return CostView(p.platform, p.percent_fee, p.percent_cap, p.flat_fee, p.dealing_fee, p.fx_fee, c.stamp_duty,
                    tuple(c.half_spreads.items()), c.default_half_spread, POT, tuple(notes))


@dataclass(frozen=True)
class StrategyLine:
    name: str
    label: str
    ret: float
    cagr: float
    max_fall: float
    sharpe: float | None
    """Yearly, from daily returns."""
    trades: int
    turnover: float
    """Yearly one-way, over the mean pot."""
    dealing: float
    spread: float
    stamp: float
    fx: float
    platform: float
    """Costs paid, GBP, on the POT start."""
    points: tuple[tuple[date, float], ...]
    """Return since the window's first close at each month end and the last close."""


@dataclass(frozen=True)
class StrategyWindow:
    window: str
    start: date
    end: date
    strategy: StrategyLine
    """After costs."""
    gross: StrategyLine
    """The same rule with no costs."""
    index: StrategyLine
    """Buy and hold after its own costs."""
    index_gross: StrategyLine
    excess: float
    """Strategy CAGR minus the index's, both after costs."""
    gross_excess: float
    dsr: float | None
    """Deflated Sharpe probability of the daily excess over the index, given the variants tried."""
    verdict: str | None
    """PASS or FAIL on confirm; None on train (reported only)."""


@dataclass(frozen=True)
class StrategyTest:
    key: str
    label: str
    rule: str
    bar_rule: str
    variants: int
    pot: float
    costs: CostView
    train: StrategyWindow
    confirm: StrategyWindow
    verdict: str
    passed: bool
    first_day: date
    last_day: date
    run_at: str
    notes: tuple[str, ...]
    id: int | None = None


def _adjusted(r: AppContext, symbol: str, before: date) -> dict[date, Session]:
    out = {}
    for b in BarRepo(r.conn).history(symbol):
        if b.date >= before or not (b.close and b.close > 0):
            continue
        k = b.adj_close / b.close if b.adj_close and b.adj_close > 0 else 1.0
        out[b.date] = Session(b.date, b.open * k if b.open and b.open > 0 else None, b.close * k)
    return out


def _line(name: str, label: str, run: StrategyRun) -> StrategyLine:
    f = figures(run.equity)
    rets = [b / a - 1 for (_, a), (_, b) in zip(run.equity, run.equity[1:]) if a > 0]
    m = moments(rets)
    c = run.costs
    base = run.equity[0][1]
    points = tuple((d, v / base - 1) for i, ((d, v), nxt) in enumerate(zip(run.equity, [*run.equity[1:], None]))
                   if i == 0 or nxt is None or (nxt[0].year, nxt[0].month) != (d.year, d.month))
    return StrategyLine(name, label, f.ret, f.cagr, f.max_fall, None if m is None else m.sharpe * 252 ** 0.5,
                        len(run.trades), run.turnover, c.dealing, c.spread, c.stamp, c.fx, run.platform, points)


def _excess_dsr(run: StrategyRun, index: StrategyRun, variants: int) -> float | None:
    held = dict(index.equity)
    pairs = [(v, held[d]) for d, v in run.equity if d in held]
    active = [(b / a) - (y / x) for (a, x), (b, y) in zip(pairs, pairs[1:]) if a > 0 and x > 0]
    m = moments(active)
    return None if m is None else deflated_sharpe(m, variants)


FREE = UkCosts(NO_FEES, half_spreads={}, default_half_spread=0.0, stamp_duty=0.0)


def _window(name: str, s: Strategy, prices: dict[str, dict[date, Session]], days: list[date], start: date, end: date,
            costs: UkCosts, variants: int, judged: bool) -> StrategyWindow:
    listings = {sym: listing_of(sym, s.bands.get(sym)) for sym in s.symbols}
    index_listing = {s.benchmark: Listing(s.benchmark, "GBP", False, None)}
    decide = s.rule(prices)
    hold = {s.benchmark: 1.0}

    def runs(c: UkCosts) -> tuple[StrategyRun, StrategyRun]:
        rule = simulate(days, prices, listings, decide, start, end, c, POT)
        index = simulate(days, {s.benchmark: prices[s.benchmark]}, index_listing, lambda d: hold, start, end, c, POT)
        return rule, index

    (net, index), (gross, index_gross) = runs(costs), runs(FREE)
    if len(net.equity) < 2:
        raise NoPrices(f"no stored {s.benchmark} sessions from {start} to {end}: fetch the series first")
    lines = (_line("strategy", f"{s.label} after costs", net), _line("gross", f"{s.label} before costs", gross),
             _line("index", "Index buy and hold after costs", index),
             _line("index_gross", "Index buy and hold before costs", index_gross))
    excess = lines[0].cagr - lines[2].cagr
    dsr = _excess_dsr(net, index, variants)
    verdict = ("PASS" if excess > 0 and dsr is not None and dsr >= DSR_BAR else "FAIL") if judged else None
    return StrategyWindow(name, net.equity[0][0], net.equity[-1][0], *lines, excess, lines[1].cagr - lines[3].cagr,
                          dsr, verdict)


def run_strategy(ctx: AppContext, key: str, registry: dict[str, Strategy] | None = None) -> StrategyTest:
    """The registered strategy `key` on train and confirm, up to the session before the run day.
    Refused unless its registration is committed; reads only."""
    s = check_strategy(key, registry)
    today = ctx.clock.utcnow().date()
    with research_context(ctx) as r:
        reg = require_committed(r, [key])[key]
        if reg.kind != "strategy":
            raise InvalidQuery(f"{key} is registered as a {reg.kind} test, not a strategy")
        prices = {sym: _adjusted(r, sym, today) for sym in {*s.symbols, s.benchmark}}
    days = sorted(prices[s.benchmark])
    if not days or days[0] > TRAIN_END or days[-1] < CONFIRM_START:
        raise NoPrices(f"no stored {s.benchmark} history from 2010 to after 2020 in the research database")
    costs = user_costs(ctx)
    train = _window("train", s, prices, days, TRAIN_START, TRAIN_END, costs, reg.variants, False)
    confirm = _window("confirm", s, prices, days, CONFIRM_START, today, costs, reg.variants, True)
    notes = [INDEX_NOTE, f"The deflated Sharpe counts {reg.variants} variant{'s' * (reg.variants != 1)} tried, from "
             "the registration.", "Cash earns nothing."]
    if confirm.gross_excess > 0 >= confirm.excess:
        notes.append("Works only before costs: beats the index gross, not after costs (a FAIL).")
    missing = sorted(sym for sym in s.symbols if not prices.get(sym))
    if missing:
        notes.append(f"No stored prices for {', '.join(missing)}: never traded.")
    return StrategyTest(key, reg.label, reg.rule, reg.bar_rule, reg.variants, POT, cost_view(ctx), train, confirm,
                        confirm.verdict, confirm.verdict == "PASS", days[0], days[-1],
                        to_utc_iso(ctx.clock.utcnow()), tuple(notes))


def keep_strategy_test(ctx: AppContext, t: StrategyTest) -> StrategyTest:
    raw = json.dumps(asdict(t), default=str)
    with research_context(ctx) as r:
        new_id = SignalTestRepo(r.conn).add(KEY_PREFIX + t.key, t.verdict, raw, t.run_at)
    return _load(raw, new_id)


def run_and_keep_strategy(ctx: AppContext, key: str, registry: dict[str, Strategy] | None = None) -> StrategyTest:
    return keep_strategy_test(ctx, run_strategy(ctx, key, registry))


def check_strategy_run(ctx: AppContext, key: str, registry: dict[str, Strategy] | None = None) -> None:
    """Refuse a job before it is queued: no code or no committed registration."""
    check_strategy(key, registry)
    check_committed(ctx, [key])


def strategy_tests(ctx: AppContext) -> dict[str, StrategyTest]:
    """The newest kept result of each strategy test."""
    with research_context(ctx) as r:
        rows = SignalTestRepo(r.conn).latest()
    return {row.signal.removeprefix(KEY_PREFIX): _load(row.result, row.id) for row in rows
            if row.signal.startswith(KEY_PREFIX)}


def _load(raw: str, row_id: int) -> StrategyTest:
    d = json.loads(raw)
    day = date.fromisoformat

    def line(x: dict) -> StrategyLine:
        return StrategyLine(**{**x, "points": tuple((day(a), v) for a, v in x["points"])})

    def window(w: dict) -> StrategyWindow:
        return StrategyWindow(**{**w, "start": day(w["start"]), "end": day(w["end"]),
                                 **{k: line(w[k]) for k in ("strategy", "gross", "index", "index_gross")}})

    c = d["costs"]
    costs = CostView(**{**c, "half_spreads": tuple(tuple(x) for x in c["half_spreads"]), "notes": tuple(c["notes"])})
    return StrategyTest(**{**d, "costs": costs, "train": window(d["train"]), "confirm": window(d["confirm"]),
                           "first_day": day(d["first_day"]), "last_day": day(d["last_day"]),
                           "notes": tuple(d["notes"]), "id": row_id})


@dataclass(frozen=True)
class CheckRow:
    window: str
    measure: str
    recorded: float
    reproduced: float
    matches: bool


def _row(window: str, measure: str, recorded: float, reproduced: float) -> CheckRow:
    return CheckRow(window, measure, recorded, reproduced,
                    abs(recorded - reproduced) <= (0 if measure == "switches" else MATCH))


@dataclass(frozen=True)
class TrendCheck:
    """The kept trend overlay result against the same rule run through the strategy engine."""
    recorded_verdict: str
    run_at: str
    last_day: date
    rows: tuple[CheckRow, ...]
    matches: bool
    note: str


def _overlay_run(sessions: list[Session], start: date, end: date, cash: Mapping[date, float] | None) -> StrategyRun:
    calls = dict(month_end_decisions(sessions))
    before = [s for s in sessions if s.day < start]
    first = next((calls[s.day] for s in reversed(before) if s.day in calls), None)
    sym = trend_service.SERIES.symbol
    costs = UkCosts(NO_FEES, stamp_duty=0.0)
    return simulate([s.day for s in sessions], {sym: {s.day: s for s in sessions}}, {sym: listing_of(sym)},
                    lambda d: None if calls.get(d) is None else {sym: 1.0 if calls[d] else 0.0}, start, end, costs,
                    1.0, initial={sym: 0.0 if first is False else 1.0}, cash_yield=cash)


def trend_check(ctx: AppContext) -> TrendCheck:
    """Re-runs the kept trend overlay's rule, over the same stored days, through the strategy
    engine with its registered cost (0.1% of the value per switch, no fees) and compares the
    figures. Nothing is judged or kept: the overlay's verdict stands as recorded."""
    kept = trend_service.trend_tests(ctx)
    if kept is None:
        raise NoPrices("no trend overlay result is kept: nothing to reproduce")
    with research_context(ctx) as r:
        sessions = [s for s in trend_service.trend_sessions(r) if s.day <= kept.last_day]
        cash = trend_service.cash_yield(r)
    rows = []
    for w, start, end, pool in (
            (kept.choose, trend_service.CHOOSE_START, trend_service.CHOOSE_END,
             [s for s in sessions if s.day <= trend_service.CHOOSE_END]),
            (kept.confirm, trend_service.CONFIRM_START, kept.last_day, sessions)):
        run = _overlay_run(pool, start, end, cash)
        f = figures(run.equity)
        rows += [_row(w.window, "return", w.trend.ret, f.ret), _row(w.window, "CAGR", w.trend.cagr, f.cagr),
                 _row(w.window, "worst fall", w.trend.max_fall, f.max_fall),
                 _row(w.window, "switches", w.switches, len(run.trades))]
    return TrendCheck(kept.verdict, kept.run_at, kept.last_day, tuple(rows), all(x.matches for x in rows),
                      "A check on the strategy engine, not a new test: the trend overlay keeps its recorded verdict "
                      "and is never judged again.")
