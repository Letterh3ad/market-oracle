"""Paper trading: three ledgers in the base currency, orders filled at the next open with
pessimistic costs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from oracle.broker.compare import benchmark_return, buy_and_hold, max_fall, returns_since
from oracle.broker.fills import COSTS, Fill, buy_fill, sell_fill
from oracle.broker.orders import Action, Side, Status
from oracle.broker.rules import Decision, Lean, Position, Rules, decide
from oracle.market.fx import RateSeries, pick_rate
from oracle.research.trace import UNVALIDATED
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, UnknownSymbol
from oracle.services.never_list import never_company, refuse_never
from oracle.services.prices_service import benchmark_history_in_base, price_history_in_base
from oracle.services.universe_service import resolve_symbol
from oracle.services.watch_service import discovered_only, own_picks
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.fx import FxRepo
from oracle.store.leans import LeanRepo
from oracle.store.paper import DecisionRepo, EquityRepo, FillRow, LoggedDecision, Order, PaperRepo
from oracle.store.research import ResearchRepo
from oracle.store.signals import SignalRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.exchanges import exchange_country

__all__ = [
    "ACCOUNTS", "AI_OFF", "DISCOVERED_OFF", "NEVER_BUY", "UNVALIDATED", "AccountScore", "AccountView", "Action",
    "Comparison", "FillOutcome", "Line",
    "Order", "Position", "Sell", "Side", "Status", "TRADERS", "account_score", "account_view", "ai_step", "comparison",
    "decision_log", "equity_history", "fill_pending", "pending_orders", "place_buy", "place_sell", "sells",
    "strategy_rules", "strategy_step", "traded_by",
]

ACCOUNTS = ("strategy", "ai", "manual")
TRADERS = ("strategy", "ai")
"""The accounts that trade by themselves."""
GONE = "no longer in the universe"
AI_OFF = ("the AI account is off: nothing was decided. Turn it on on the Ledgers tab, "
          "or set [ai_account] enabled = true in oracle.toml")
DISCOVERED_OFF = ("discovered, and the AI account does not trade discovered companies: turn it on in Settings "
                  "([discovery] ai_trades)")
NEVER_BUY = "on the never list: never bought"


@dataclass(frozen=True)
class AccountView:
    name: str
    cash: float
    positions: tuple[Position, ...]
    pending: tuple[Order, ...]
    realised: float
    """Profit or loss locked in by sells, in the base currency, after all costs."""
    rate_sources: tuple[str, ...] = ()
    """Where the rates behind its figures came from (position values and past fills), for
    attribution."""

    @property
    def holdings(self) -> float:
        """Positions at their last close; one without a rate yet counts as nothing."""
        return sum(p.value for p in self.positions if p.value is not None)

    @property
    def equity(self) -> float:
        return self.cash + self.holdings


@dataclass(frozen=True)
class AccountScore:
    name: str
    equity: float
    """Cash plus positions at the last close, base currency."""
    start: float
    realised: float
    unrealised: float
    costs: float
    """Fees, stamp duty, FX spread and slippage paid, base currency."""
    max_fall: float
    """Largest fall from a peak in the daily equity record, as a negative ratio."""

    @property
    def return_(self) -> float:
        return self.equity / self.start - 1


@dataclass(frozen=True)
class Sell:
    date: date
    symbol: str
    shares: int
    proceeds: float
    """Cash received in the base currency, after all costs."""
    cost: float
    """What the shares sold had cost, at the average cost basis."""

    @property
    def realised(self) -> float:
        return self.proceeds - self.cost


@dataclass(frozen=True)
class Line:
    """One line of the comparison, from the comparison's start (or its own first record)."""
    name: str
    caption: str
    points: tuple[tuple[date, float], ...]
    """(day, return since the start as a ratio), 0.0 on the first day; empty with no history."""
    max_fall: float
    trades: int
    costs: float
    """Paid since the start, base currency."""
    label: str = ""
    """Display name for a benchmark line (the universe's company name, or a nice fallback);
    empty for an account or buy-and-hold line, whose label the page already knows."""

    @property
    def return_(self) -> float | None:
        return self.points[-1][1] if self.points else None


@dataclass(frozen=True)
class Comparison:
    start: date | None
    """The AI account's first equity record; None before it has one."""
    lines: tuple[Line, ...]
    """The accounts in ACCOUNTS order, then buy_and_hold."""
    left_out: tuple[str, ...]
    """Watched listings buy-and-hold could not hold: no prices from the start on."""
    notes: tuple[str, ...]


@dataclass(frozen=True)
class FillOutcome:
    order: Order
    status: Status
    note: str | None


def place_buy(ctx: AppContext, symbol: str, cash: float, account: str = "manual", source: str = "manual") -> Order:
    """Queue a buy of `cash` (base currency); it fills at the next open."""
    if not (math.isfinite(cash) and cash > 0):
        raise InvalidQuery(f"a buy must spend more than nothing, not {cash:,.2f}")
    listing = resolve_symbol(ctx, symbol).symbol
    refuse_never(ctx, listing)
    now = ctx.clock.utcnow()
    return PaperRepo(ctx.conn).add_order(account, listing, Side.BUY, cash, None, to_utc_iso(now), now.date(), source)


def place_sell(ctx: AppContext, symbol: str, shares: int | None, account: str = "manual",
               source: str = "manual") -> Order:
    """Queue a sell of `shares` (None: the whole position); long only, so never more than
    held and not already queued for sale."""
    repo = PaperRepo(ctx.conn)
    listing = _sellable(ctx, repo, account, symbol)
    held = _held_shares(repo, account, listing)
    if not held:
        raise InvalidQuery(f"{listing} is not held on the {account} account")
    queued = [o for o in repo.pending(account) if o.symbol == listing and o.side == Side.SELL]
    free = 0 if any(o.shares is None for o in queued) else held - sum(o.shares for o in queued)
    if free <= 0:
        raise InvalidQuery(f"all {held} {listing} shares already have a pending sell")
    if shares is not None and not 0 < shares <= free:
        raise InvalidQuery(f"can sell 1 to {free} {listing} shares, not {shares}")
    now = ctx.clock.utcnow()
    return repo.add_order(account, listing, Side.SELL, None, shares,
                          to_utc_iso(now), now.date(), source)


def _sellable(ctx: AppContext, repo: PaperRepo, account: str, symbol: str) -> str:
    """As stored in the universe; a held listing gone from it can still be sold."""
    try:
        return resolve_symbol(ctx, symbol).symbol
    except UnknownSymbol:
        held = next((s for s in _replay(repo, account)[0] if s.casefold() == symbol.strip().casefold()), None)
        if held is None:
            raise
        return held


def strategy_step(ctx: AppContext) -> list[Decision]:
    """Run the strategy rules over every watched listing's latest lean and queue the
    resulting orders on the strategy account. Every decision, skips included, is logged."""
    found, skips = _usable_leans(ctx, LeanRepo(ctx.conn), traded_by(ctx, "strategy"))
    return _step(ctx, "strategy", found, skips, {})


def ai_step(ctx: AppContext) -> list[Decision] | None:
    """The strategy's rules, limits and fills over each watched listing's latest research
    verdict instead of its lean, on the AI account; None, doing nothing, while that account
    is off. Every decision is logged with the research run its verdict came from."""
    if not ctx.config.ai_account.enabled:
        return None
    traded = traded_by(ctx, "ai")
    found, skips, runs = _usable_verdicts(ctx, traded)
    skips += [Decision(s, Action.SKIP, None, DISCOVERED_OFF) for s in discovered_only(ctx) if s not in traded]
    return _step(ctx, "ai", found, skips, runs)


def traded_by(ctx: AppContext, account: str) -> list[str]:
    """The listings a trading account decides over. The strategy account keeps to the
    user's own picks, so its record stays a fair test of the rules on them; the AI account
    adds discovered ones while `[discovery] ai_trades` is on. Whatever the account holds
    stays in, off every list or not, so a position is never stranded: the rules only ever
    buy what is not held, so a held listing can be sold but never added to."""
    listed = own_picks(ctx)
    if account == "ai" and ctx.config.discovery.ai_trades:
        listed += discovered_only(ctx)
    return sorted(set(listed) | set(PaperRepo(ctx.conn).held_symbols(account)))


def _step(ctx: AppContext, account: str, found: dict[str, Lean], unusable: list[Decision],
          reports: dict[str, int]) -> list[Decision]:
    """Decide over `found` with the strategy's rules, queue the orders on `account` and log
    every decision; `reports` maps a listing to the research run behind its decision."""
    view = account_view(ctx, account)
    holdings = {p.symbol: p for p in view.positions}
    barred = _never_bought(ctx, [s for s in found if s not in holdings])
    found = {s: lean for s, lean in found.items() if s not in barred}
    unusable = unusable + [Decision(s, Action.SKIP, None, NEVER_BUY) for s in barred]
    sizes = {s: size for s in found if (size := _risk_size(ctx, s)) is not None}
    orders = PaperRepo(ctx.conn).pending(account)
    committed = sum(o.cash for o in orders if o.side == Side.BUY)
    decided = decide(found, holdings, view.cash - committed, view.equity, sizes, strategy_rules(ctx),
                     {o.symbol for o in orders})
    # One decision per listing: a sell or a pending order says more than a lean problem.
    covered = {o.symbol for o in orders} | {d.symbol for d in decided if d.action == Action.SELL}
    decisions = [d for d in unusable if d.symbol not in covered] + decided
    today, log = ctx.clock.utcnow().date(), DecisionRepo(ctx.conn)
    for d in decisions:
        order = None
        if d.action == Action.BUY:
            order = place_buy(ctx, d.symbol, d.cash, account, source=account)
        elif d.action == Action.SELL:
            order = place_sell(ctx, d.symbol, None, account, source=account)
        # Logged as each order is placed, so one failing never leaves an order unexplained.
        log.log([LoggedDecision(account, today, d.symbol, d.action, d.cash, d.reason, order.id if order else None,
                                reports.get(d.symbol), d.needs_research)])
    return decisions


def _never_bought(ctx: AppContext, symbols: list[str]) -> set[str]:
    """The user's rule bars a company everywhere; `[discovery] never` only what discovery
    alone brought in."""
    discovered = set(discovered_only(ctx))
    return {s for s in symbols if never_company(ctx, s, user=s in discovered)}


def strategy_rules(ctx: AppContext) -> Rules:
    """The fixed rules both trading accounts and the backtest share, from the settings."""
    return Rules(ctx.config.strategy.min_probability, ctx.config.risk.max_position,
                 ctx.config.strategy.max_positions, ctx.config.risk.cash_floor, ctx.config.strategy.stop_loss)


def _usable_leans(ctx: AppContext, leans: LeanRepo, watched: list[str]) -> tuple[dict[str, Lean], list[Decision]]:
    """A lean older than the listing's last bar is out of date: never trade on it."""
    bars, universe, found, skips = BarRepo(ctx.conn), UniverseRepo(ctx.conn), {}, []
    for symbol in watched:
        lean = leans.latest(symbol)
        last = bars.last_date(symbol)
        if universe.resolve(symbol) is None:
            skips.append(Decision(symbol, Action.SKIP, None, GONE))
        elif lean is None:
            skips.append(Decision(symbol, Action.SKIP, None, "no lean yet: analyse it first"))
        elif last is not None and lean.as_of < last:
            skips.append(Decision(symbol, Action.SKIP, None, f"{lean.source} lean is out of date: analyse it again"))
        else:
            found[symbol] = lean
    return found, skips


def _usable_verdicts(ctx: AppContext, watched: list[str]) -> tuple[dict[str, Lean], list[Decision], dict[str, int]]:
    """Each listing's newest live report's verdict as a lean, unless it has none (a rejected
    verdict is never passed over for an older one) or it is older than the configured age;
    with the research run behind each, skips included."""
    research, universe, today = ResearchRepo(ctx.conn), UniverseRepo(ctx.conn), ctx.clock.utcnow().date()
    max_age = ctx.config.ai_account.max_verdict_age_days
    found, skips, runs = {}, [], {}
    for symbol in watched:
        run = research.latest_report(symbol)
        if universe.resolve(symbol) is None:
            skips.append(Decision(symbol, Action.SKIP, None, GONE))
            continue
        if run is None:
            skips.append(Decision(symbol, Action.SKIP, None, "no verdict yet: run research on it", needs_research=True))
            continue
        runs[symbol] = run.id
        if (v := run.report.verdict) is None:
            skips.append(Decision(symbol, Action.SKIP, None, f"no verdict: {run.report.no_verdict}"))
        elif (today - run.report.day).days > max_age:
            skips.append(Decision(symbol, Action.SKIP, None, f"the verdict is from {run.report.day.isoformat()}, older "
                                                             f"than {max_age} days: run research on it again",
                                  needs_research=True))
        else:
            found[symbol] = Lean(symbol, run.report.day, v.label, v.confidence, "ai")
    return found, skips, runs


def decision_log(ctx: AppContext, account: str, limit: int = 50) -> list[LoggedDecision]:
    """An account's recent decisions, newest first."""
    return DecisionRepo(ctx.conn).recent(account, limit)


def _risk_size(ctx: AppContext, symbol: str) -> float | None:
    risk = next((s for s in SignalRepo(ctx.conn).latest(symbol) if s.analyst == "risk"), None)
    return dict(risk.figures).get("size_share") if risk else None


def fill_pending(ctx: AppContext) -> list[FillOutcome]:
    """Fill every order whose next bar is stored, then record each account's equity for
    today. Orders still waiting for a bar are left out; ones blocked by something else
    are reported with the reason."""
    repo = PaperRepo(ctx.conn)
    out = []
    for order in repo.pending():
        outcome = _try_fill(ctx, repo, order)
        if outcome is not None:
            out.append(outcome)
    _record_equity(ctx)
    return out


def account_score(ctx: AppContext, account: str) -> AccountScore:
    view = account_view(ctx, account)
    fills = PaperRepo(ctx.conn).fills(account)
    unrealised = sum(p.value - p.cost for p in view.positions if p.value is not None)
    costs = sum(_cost(f) for f in fills)
    return AccountScore(account, view.equity, ctx.config.risk.account, view.realised, unrealised, costs,
                        max_fall(EquityRepo(ctx.conn).history(account)))


def _cost(f: FillRow) -> float:
    """Fees, stamp duty, FX spread and slippage of one fill, base currency."""
    return (f.fee + f.stamp) / f.rate + f.fx + f.slippage


def equity_history(ctx: AppContext, account: str) -> list[tuple]:
    """(date, equity) per day a fill run happened, oldest first."""
    return EquityRepo(ctx.conn).history(account)


CAPTIONS = {
    "strategy": "Strategy: trades the leans under the fixed rules.",
    "ai": "AI: trades the research verdicts under the same rules.",
    "manual": "Manual: your own buys and sells.",
}
NOT_VALIDATED = f"Nothing here is validated yet, so no line proves anything ({UNVALIDATED})."
BENCHMARK_LABELS = {"VWRL.L": "World (VWRL.L)", "VUSA.L": "S&P 500 (VUSA.L)"}
"""Nice fallback labels for the two default benchmarks, used when the symbol is not (or
not yet) in the universe under its own company name."""
BENCHMARK_DESCRIPTIONS = {"VWRL.L": "the whole world market", "VUSA.L": "the S&P 500"}


def comparison(ctx: AppContext) -> Comparison:
    """Every account and an equal-weight buy-and-hold of today's watchlist, each as its
    return since the day the AI account started, with its largest fall, trades and costs."""
    equity = EquityRepo(ctx.conn)
    histories = {name: equity.history(name) for name in ACCOUNTS}
    start = histories["ai"][0][0] if histories["ai"] else None
    lines = [_account_line(ctx, name, history, start) for name, history in histories.items()]
    if start is None:
        notes = ("The AI account has no history yet: each account is shown from its own first record, and buy and "
                 "hold starts once the AI account has one.", NOT_VALIDATED)
        lines.append(Line("buy_and_hold", "Buy and hold: starts with the AI account.", (), 0.0, 0, 0.0))
        lines += [_benchmark_line(ctx, s, None) for s in ctx.config.compare.benchmarks]
        return Comparison(None, tuple(lines), (), notes)
    closes = {}
    for symbol in own_picks(ctx):
        try:
            closes[symbol] = [(c.bar.date, c.close_base) for c in price_history_in_base(ctx, symbol)]
        except UnknownSymbol:
            closes[symbol] = []
    held, left_out = buy_and_hold(closes, start, ctx.config.risk.account)
    n = len(closes) - len(left_out)
    lines.append(Line("buy_and_hold", f"Buy and hold: equal amounts of the {n} listing{'' if n == 1 else 's'} "
                                      f"watched today, bought at the first close from {start.isoformat()} and never "
                                      "sold, with no costs.",
                      tuple(returns_since(held, start)), max_fall(held), 0, 0.0))
    lines += [_benchmark_line(ctx, s, start) for s in ctx.config.compare.benchmarks]
    notes = [f"Each line is the return since {start.isoformat()}, the day the AI account started.", NOT_VALIDATED]
    if left_out:
        notes.append(f"Left out of buy and hold, no prices from {start.isoformat()} on: {', '.join(left_out)}.")
    if found := discovered_only(ctx):
        notes.append(f"Buy and hold measures your own picks, so the discovered listings are left out: "
                     f"{', '.join(found)}.")
    return Comparison(start, tuple(lines), tuple(left_out), tuple(notes))


def _benchmark_line(ctx: AppContext, symbol: str, start: date | None) -> Line:
    """Holding `symbol` from `start`, no costs: its own line, independent of the watchlist
    and never traded. `start` None (the AI account has no history yet) is an empty line, like
    every other line in that state."""
    label = _benchmark_label(ctx, symbol)
    if start is None:
        return Line(symbol, _benchmark_caption(symbol, None), (), 0.0, 0, 0.0, label)
    closes = [(c.bar.date, c.close_base) for c in benchmark_history_in_base(ctx, symbol)]
    usable = [(d, c) for d, c in closes if d >= start and c is not None]
    points = tuple(benchmark_return(closes, start))
    return Line(symbol, _benchmark_caption(symbol, start), points, max_fall(usable), 0, 0.0, label)


def _benchmark_label(ctx: AppContext, symbol: str) -> str:
    found = UniverseRepo(ctx.conn).resolve(symbol)
    return found.name if found else BENCHMARK_LABELS.get(symbol, symbol)


def _benchmark_caption(symbol: str, start: date | None) -> str:
    if start is None:
        return f"Holding {symbol} from the AI account's start."
    where = BENCHMARK_DESCRIPTIONS.get(symbol)
    return f"Holding {symbol}{f' ({where})' if where else ''} from {start.isoformat()}, no costs."


def _account_line(ctx: AppContext, name: str, history: list[tuple], start: date | None) -> Line:
    since = start or (history[0][0] if history else None)
    if since is None:
        return Line(name, CAPTIONS[name], (), 0.0, 0, 0.0)
    kept = [(d, v) for d, v in history if d >= since]
    fills = [f for f in PaperRepo(ctx.conn).fills(name) if f.date >= since]
    return Line(name, CAPTIONS[name], tuple(returns_since(kept, since)), max_fall(kept), len(fills),
                sum(_cost(f) for f in fills))


def _record_equity(ctx: AppContext) -> None:
    """The AI account only while it is on, so the comparison starts the day it was first turned on."""
    today = ctx.clock.utcnow().date()
    for account in ACCOUNTS:
        if account == "ai" and not ctx.config.ai_account.enabled:
            continue
        view = account_view(ctx, account)
        EquityRepo(ctx.conn).record(account, today, view.cash, view.holdings)


def pending_orders(ctx: AppContext) -> list[Order]:
    """Every account's orders still waiting to fill."""
    repo = PaperRepo(ctx.conn)
    return [o for a in ACCOUNTS for o in repo.pending(a)]


def account_view(ctx: AppContext, account: str) -> AccountView:
    repo = PaperRepo(ctx.conn)
    held, realised = _replay(repo, account)
    positions, sources = _positions(ctx, held)
    sources |= _fill_rate_sources(ctx, repo, account)
    return AccountView(account, _cash(ctx, repo, account), tuple(positions), tuple(repo.pending(account)),
                       realised, tuple(sorted(sources)))


def _try_fill(ctx: AppContext, repo: PaperRepo, order: Order) -> FillOutcome | None:
    # A never rule set after the order was placed (or a discovery-only one, for a discovered
    # symbol) still blocks it before it spends real-looking cash.
    if order.side == Side.BUY and never_company(ctx, order.symbol, user=order.symbol in discovered_only(ctx)):
        return _reject(repo, order, "on the never list")
    profile = UniverseRepo(ctx.conn).profile(order.symbol)
    if profile is None and order.side == Side.BUY:
        # Its exchange, and so any stamp duty, is unknown.
        return _reject(repo, order, f"{order.symbol} is no longer in the universe")
    # A bar without an open (a data gap) cannot fill; the next one with an open does.
    history = BarRepo(ctx.conn).history(order.symbol)
    bar = next((b for b in history if b.date > order.placed_on and b.open is not None), None)
    if bar is None:
        return None
    rate = _rate(ctx, bar.currency, bar.date)
    if rate is None:
        note = f"no {bar.currency} rate for {bar.date.isoformat()}: update FX rates first"
        repo.set_status(order.id, Status.PENDING, note)
        return FillOutcome(order, Status.PENDING, note)
    if profile is None:
        # A held listing gone from the universe still sells, at the dearest fee band.
        exchange, country = "", ""
    else:
        exchange = profile.exchange or ""
        country = exchange_country(exchange) or profile.country or ""
    if order.side == Side.SELL:
        held = _held_shares(repo, order.account, order.symbol)
        shares = order.shares if order.shares is not None else held
        if not 0 < shares <= held:
            return _reject(repo, order, f"only {held} shares held")
        fill = sell_fill(shares, open_price=bar.open, rate=rate, country=country, costs=COSTS)
        repo.record_fill(_row(order, bar.date, bar.currency, rate, fill))
        return FillOutcome(order, Status.FILLED, None)
    cash = _cash(ctx, repo, order.account)
    if order.cash > cash:
        return _reject(repo, order, f"not enough cash: {order.cash:,.2f} asked, {cash:,.2f} available")
    fill = buy_fill(order.cash, open_price=bar.open, rate=rate, country=country, exchange=exchange,
                    costs=COSTS)
    if fill.shares == 0:
        return _reject(repo, order, f"{order.cash:,.2f} buys no whole share at {bar.open:,.2f} {bar.currency} after costs")
    repo.record_fill(_row(order, bar.date, bar.currency, rate, fill))
    return FillOutcome(order, Status.FILLED, None)


def _reject(repo: PaperRepo, order: Order, note: str) -> FillOutcome:
    repo.set_status(order.id, Status.REJECTED, note)
    return FillOutcome(order, Status.REJECTED, note)


def _row(order: Order, day, currency: str, rate: float, fill: Fill) -> FillRow:
    return FillRow(order.id, order.account, order.symbol, order.side, day, fill.shares, fill.price, currency, rate,
                   fill.cash, fill.fee, fill.stamp, fill.fx, fill.slippage)


def _rate(ctx: AppContext, currency: str, day) -> float | None:
    if currency == ctx.config.base_currency:
        return 1.0
    picked = pick_rate(FxRepo(ctx.conn, ctx.config.base_currency).history(currency), day)
    return picked.units_per_base if picked else None


def _fill_rate_sources(ctx: AppContext, repo: PaperRepo, account: str) -> set[str]:
    """Proceeds, costs and realised results keep the rates their fills used, long after the
    position that needed them is gone."""
    days: dict[str, set] = {}
    for f in repo.fills(account):
        if f.currency != ctx.config.base_currency:
            days.setdefault(f.currency, set()).add(f.date)
    fx = FxRepo(ctx.conn, ctx.config.base_currency)
    out = set()
    for currency, dates in days.items():
        history = RateSeries(fx.history(currency))
        out |= {r.source for d in dates if (r := history.pick(d))}
    return out


def _cash(ctx: AppContext, repo: PaperRepo, account: str) -> float:
    return ctx.config.risk.account + sum(f.cash for f in repo.fills(account))


def _held_shares(repo: PaperRepo, account: str, symbol: str) -> int:
    return _replay(repo, account)[0].get(symbol, [0, 0.0])[0]


def _replay(repo: PaperRepo, account: str) -> tuple[dict[str, list], float]:
    """({symbol: [shares, cost]}, realised) from the account's fills, at average cost."""
    held, done = _replay_sells(repo, account)
    return held, sum(s.realised for s in done)


def _replay_sells(repo: PaperRepo, account: str) -> tuple[dict[str, list], list[Sell]]:
    held: dict[str, list] = {}
    done = []
    for f in repo.fills(account):
        shares, cost = held.get(f.symbol, [0, 0.0])
        if f.side == Side.BUY:
            held[f.symbol] = [shares + f.shares, cost - f.cash]
        else:
            sold_cost = cost * f.shares / shares
            done.append(Sell(f.date, f.symbol, f.shares, f.cash, sold_cost))
            held[f.symbol] = [shares - f.shares, cost - sold_cost]
    return held, done


def sells(ctx: AppContext, account: str) -> list[Sell]:
    """Every filled sell on the account with what it realised, oldest first."""
    return _replay_sells(PaperRepo(ctx.conn), account)[1]


def _positions(ctx: AppContext, held: dict[str, list]) -> tuple[list[Position], set[str]]:
    """Positions at their last close, and the rate sources those values used."""
    out, sources = [], set()
    for symbol, (shares, cost) in sorted(held.items()):
        if shares:
            last = price_history_in_base(ctx, symbol, days=1)
            value = shares * last[-1].close_base if last and last[-1].close_base is not None else None
            if value is not None and last[-1].rate_source:
                sources.add(last[-1].rate_source)
            out.append(Position(symbol, int(shares), cost, value))
    return out, sources
