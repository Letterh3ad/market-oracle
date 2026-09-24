"""The strategy's fixed rules replayed over stored bars, one day at a time, in memory. Each
day's leans come from bars up to that day only; orders fill at the next bar's open (or close).
The leans are the technicals baseline unless another source is passed: the replay study's
verdicts, or a baseline account's."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from oracle.analysts.indicators import MIN_BARS, YEAR
from oracle.analysts.risk import Priced, Series, base_series
from oracle.analysts.risk import risk as risk_signal
from oracle.analysts.signals import AnalystSignal
from oracle.analysts.technicals import price_signals
from oracle.broker.baseline import baseline_lean
from oracle.broker.fills import Costs, Fill, buy_fill, costs_for, sell_fill
from oracle.broker.orders import Action, Side
from oracle.broker.rules import BUY_LABELS, Decision, Lean, Position, Rules, decide
from oracle.market.bars import Bar

LOOKBACK = YEAR + MIN_BARS
"""Bars each day's signals see: the trend's year and the volatility's year of readings, with
room for the RSI and MACD averages to forget their start (a trace of a billionth). A full
history would make a long replay grow with the square of its length."""

LeanSource = Callable[[str, Sequence[Bar]], Lean | None]
"""A listing's lean after the close of the last of its bars given (up to LOOKBACK of them)."""


@dataclass(frozen=True)
class Venue:
    """Where a listing trades, for the paper accounts' fee table and stamp duty, and its size band
    for their slippage."""
    country: str
    exchange: str
    band: str | None = None


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
    """Slippage, fees, stamp duty and FX spread, base currency."""
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
           risk: Mapping[str, float], slippage: float, commission: float, lean: LeanSource | None = None,
           fill_at: Literal["open", "close"] = "open", venues: Mapping[str, Venue] | None = None) -> Replay:
    """`history` is each listing's bars, oldest first, with that day's rate. `risk` holds
    the risk analyst's settings; its `account` is the starting cash. `commission` is a flat
    fee per trade in the base currency. `lean` defaults to the technicals baseline. With
    `venues`, every fill pays the paper accounts' costs instead (`fills.costs_for` the
    listing's size band and the fee table for its venue, dearest without one), and
    `slippage` and `commission` are not used."""
    lean = lean or technicals_lean
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
            if p is None or _price(p.bar, fill_at) is None or p.rate is None:
                waiting.append(order)
                continue
            trade, note = _fill(order, p, _price(p.bar, fill_at), cash, held, _charges(
                order.decision.symbol, p, venues, slippage, commission))
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
                picked = lean(s, bars)
                # Only a buy is sized, and sizing is most of a replay's time.
                size = _size(s, base[s][:base_seen[s]], risk) if picked and picked.label in BUY_LABELS else None
                leans[s] = (last, picked, size)
        found = {s: x for s, (_, x, _) in leans.items() if x is not None}
        sizes = {s: size for s, (_, _, size) in leans.items() if size is not None}
        committed = sum(o.decision.cash for o in pending if o.decision.action == Action.BUY)
        for d in decide(found, positions, cash - committed, total, sizes, rules, {o.decision.symbol for o in pending}):
            if d.action != Action.SKIP:
                pending.append(_Order(day, d))
                orders.append((day, d))
    return Replay(tuple(equity), tuple(trades), tuple(orders), tuple(rejected),
                  tuple(sorted({o.decision.symbol for o in pending})))


def technicals_lean(symbol: str, bars: Sequence[Bar]) -> Lean | None:
    """The technicals-only baseline lean."""
    technicals = [s for s in price_signals(list(bars), symbol)
                  if isinstance(s, AnalystSignal) and s.analyst == "technicals"]
    return baseline_lean(symbol, technicals)


def _size(symbol: str, series: Series, settings: Mapping[str, float]) -> float | None:
    """The risk analyst's size as a share of equity."""
    # Its size reads only the last year of returns; the rest of the history is display.
    sized = risk_signal(symbol, series[-(YEAR + 1):], {}, currency="", **settings)
    return dict(sized.figures)["size_share"] if isinstance(sized, AnalystSignal) else None


def _price(bar: Bar, fill_at: str) -> float | None:
    return bar.close if fill_at == "close" else bar.open


def _charges(symbol: str, p: Priced, venues: Mapping[str, Venue] | None, slippage: float,
             commission: float) -> tuple[Costs, Venue]:
    if venues is None:
        return Costs(slippage=slippage, fx_spread=0.0, stamp_duty=0.0, commission=commission * p.rate), Venue("", "")
    venue = venues.get(symbol, Venue("", ""))
    return costs_for(venue.band), venue


def _fill(order: _Order, p: Priced, price: float, cash: float, held: Mapping[str, list],
          charges: tuple[Costs, Venue]) -> tuple[Trade | None, str | None]:
    d, bar = order.decision, p.bar
    costs, venue = charges
    if d.action == Action.SELL:
        fill = sell_fill(held[d.symbol][0], open_price=price, rate=p.rate, country=venue.country, costs=costs)
    elif d.cash > cash:
        return None, f"{bar.date.isoformat()} {d.symbol}: not enough cash to buy ({d.cash:,.2f} asked, {cash:,.2f} left)"
    else:
        fill = buy_fill(d.cash, open_price=price, rate=p.rate, country=venue.country, exchange=venue.exchange,
                        costs=costs)
        if fill.shares == 0:
            return None, f"{bar.date.isoformat()} {d.symbol}: {d.cash:,.2f} buys no whole share after costs"
    return _trade(bar.date, d, bar.currency, p.rate, fill), None


def _trade(day: date, d: Decision, currency: str, rate: float, fill: Fill) -> Trade:
    side = Side.BUY if d.action == Action.BUY else Side.SELL
    return Trade(day, d.symbol, side, fill.shares, fill.price, currency, fill.cash, fill_costs(fill, rate), d.reason)


def fill_costs(fill: Fill, rate: float) -> float:
    """Everything a fill paid beyond the price, in the base currency."""
    return (fill.fee + fill.stamp) / rate + fill.slippage + fill.fx
