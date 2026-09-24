"""The strategy's fixed rules replayed over stored bars, one day at a time, in memory. Each
day's leans come from bars up to that day only; orders fill at the next open."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from oracle.analysts.indicators import MIN_BARS, YEAR
from oracle.analysts.risk import Priced, Series, base_series
from oracle.analysts.risk import risk as risk_signal
from oracle.analysts.signals import AnalystSignal
from oracle.analysts.technicals import price_signals
from oracle.broker.baseline import baseline_lean
from oracle.broker.fills import Costs, Fill, buy_fill, sell_fill
from oracle.broker.orders import Action, Side
from oracle.broker.rules import Decision, Lean, Position, Rules, decide

LOOKBACK = YEAR + MIN_BARS
"""Bars each day's signals see: the trend's year and the volatility's year of readings, with
room for the RSI and MACD averages to forget their start (a trace of a billionth). A full
history would make a long replay grow with the square of its length."""


@dataclass(frozen=True)
class Trade:
    day: date
    symbol: str
    side: Side
    shares: int
    price: float
    """Per share after slippage, in the listing's currency."""
    currency: str
    cash: float
    """Change to cash in the base currency: negative for a buy."""
    costs: float
    """Slippage and commission, base currency."""
    reason: str
    """Why the rules placed the order."""


@dataclass(frozen=True)
class Replay:
    equity: tuple[tuple[date, float], ...]
    """Cash plus positions at each day's close, base currency."""
    trades: tuple[Trade, ...]
    orders: tuple[tuple[date, Decision], ...]
    """Every buy and sell the rules placed, by the day they were decided."""
    rejected: tuple[str, ...]
    """Orders that could not fill, with why."""
    unfilled: tuple[str, ...]
    """Listings with an order still waiting at the end."""


@dataclass
class _Order:
    placed: date
    decision: Decision


def replay(history: Mapping[str, Sequence[Priced]], start: date, end: date, *, rules: Rules,
           risk: Mapping[str, float], slippage: float, commission: float) -> Replay:
    """`history` is each listing's bars, oldest first, with that day's rate. `risk` holds
    the risk analyst's settings; its `account` is the starting cash. `commission` is a flat
    fee per trade in the base currency."""
    capital = risk["account"]
    days = sorted({p.bar.date for rows in history.values() for p in rows if start <= p.bar.date <= end})
    seen = dict.fromkeys(history, 0)
    base = {s: base_series(rows) for s, rows in history.items()}
    base_seen = dict.fromkeys(history, 0)
    last_value: dict[str, float] = {}
    held: dict[str, list] = {}
    cash = capital
    pending: list[_Order] = []
    leans: dict[str, tuple[date, Lean | None, float | None]] = {}
    equity, trades, orders, rejected = [], [], [], []
    for day in days:
        today = {}
        for s, rows in history.items():
            while seen[s] < len(rows) and rows[seen[s]].bar.date <= day:
                seen[s] += 1
            if seen[s] and rows[seen[s] - 1].bar.date == day:
                today[s] = rows[seen[s] - 1]
            while base_seen[s] < len(base[s]) and base[s][base_seen[s]][0] <= day:
                base_seen[s] += 1
        waiting = []
        for order in pending:
            p = today.get(order.decision.symbol)
            if p is None or p.bar.open is None or p.rate is None:
                waiting.append(order)
                continue
            trade, note = _fill(order, p, cash, held, slippage, commission)
            if note:
                rejected.append(note)
                continue
            cash += trade.cash
            shares, cost = held.get(trade.symbol, [0, 0.0])
            if trade.side == Side.BUY:
                held[trade.symbol] = [shares + trade.shares, cost - trade.cash]
            else:
                held.pop(trade.symbol)
            trades.append(trade)
        pending = waiting
        for s, p in today.items():
            if p.rate:
                last_value[s] = p.bar.close / p.rate
        positions = {s: Position(s, n, cost, n * last_value[s] if s in last_value else None)
                     for s, (n, cost) in held.items()}
        total = cash + sum(p.value for p in positions.values() if p.value is not None)
        equity.append((day, total))
        # Nothing decided on the last day could fill inside the replay.
        if day == days[-1]:
            continue
        for s, rows in history.items():
            if not seen[s]:
                continue
            last = rows[seen[s] - 1].bar.date
            if s not in leans or leans[s][0] != last:
                bars = [p.bar for p in rows[max(0, seen[s] - LOOKBACK):seen[s]]]
                leans[s] = (last, *_lean(s, bars, base[s][:base_seen[s]], risk))
        found = {s: lean for s, (_, lean, _) in leans.items() if lean is not None}
        sizes = {s: size for s, (_, _, size) in leans.items() if size is not None}
        committed = sum(o.decision.cash for o in pending if o.decision.action == Action.BUY)
        for d in decide(found, positions, cash - committed, total, sizes, rules, {o.decision.symbol for o in pending}):
            if d.action != Action.SKIP:
                pending.append(_Order(day, d))
                orders.append((day, d))
    return Replay(tuple(equity), tuple(trades), tuple(orders), tuple(rejected),
                  tuple(sorted({o.decision.symbol for o in pending})))


def _lean(symbol: str, bars: list, series: Series, settings: Mapping[str, float]) -> tuple[Lean | None, float | None]:
    """The technicals-only baseline lean and the risk analyst's size as a share of equity."""
    technicals = [s for s in price_signals(bars, symbol) if isinstance(s, AnalystSignal) and s.analyst == "technicals"]
    lean = baseline_lean(symbol, technicals)
    # Its size reads only the last year of returns; the rest of the history is display.
    sized = risk_signal(symbol, series[-(YEAR + 1):], {}, currency="", **settings)
    return lean, dict(sized.figures)["size_share"] if isinstance(sized, AnalystSignal) else None


def _fill(order: _Order, p: Priced, cash: float, held: Mapping[str, list], slippage: float,
          commission: float) -> tuple[Trade | None, str | None]:
    d, bar = order.decision, p.bar
    costs = Costs(slippage=slippage, fx_spread=0.0, stamp_duty=0.0, commission=commission * p.rate)
    if d.action == Action.SELL:
        fill = sell_fill(held[d.symbol][0], open_price=bar.open, rate=p.rate, country="", costs=costs)
    elif d.cash > cash:
        return None, f"{bar.date.isoformat()} {d.symbol}: not enough cash to buy ({d.cash:,.2f} asked, {cash:,.2f} left)"
    else:
        fill = buy_fill(d.cash, open_price=bar.open, rate=p.rate, country="", exchange="", costs=costs)
        if fill.shares == 0:
            return None, f"{bar.date.isoformat()} {d.symbol}: {d.cash:,.2f} buys no whole share after costs"
    return _trade(bar.date, d, bar.currency, p.rate, fill), None


def _trade(day: date, d: Decision, currency: str, rate: float, fill: Fill) -> Trade:
    side = Side.BUY if d.action == Action.BUY else Side.SELL
    return Trade(day, d.symbol, side, fill.shares, fill.price, currency, fill.cash, fill.fee / rate + fill.slippage,
                 d.reason)
