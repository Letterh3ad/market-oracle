"""Core-satellite: most of the money in an equal-weight buy-and-hold of the main list (the core),
set back to equal weight once a year, the rest (the satellite) traded by the regime blend under
the strategy's rules within its own cash. Fixed a priori (spec decision 7): 75/25, equal weight,
yearly. The rebalance planning is shared by the replayed line and the live account.

A rebalance is two steps: the day it falls due, sells trim the overweights and drop listings no
longer in the core; once they have filled, buys top the underweights up from the core's cash only.
With nothing to trim (the first day) the buys go the same day."""

from __future__ import annotations

import calendar
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from oracle.analysts.risk import Priced
from oracle.broker.backtest import LeanCap, LeanSource, Replay, Trade, Venue, _charges, _price, fill_costs, replay
from oracle.broker.fills import buy_fill, sell_fill
from oracle.broker.orders import Action, Side
from oracle.broker.rules import Decision, Rules

CORE_SHARE = 0.75
"""The core's share of the starting capital; the satellite has the rest."""
REBALANCE_YEARS = 1
REBALANCE = "core rebalance"
"""How every core order's reason starts."""
STALE = timedelta(days=7)
"""A member whose last close is older than this on a rebalance day is left out of that one's targets."""


@dataclass(frozen=True)
class CoreOrder:
    symbol: str
    side: Side
    cash: float | None
    """A buy's amount, base currency."""
    shares: int | None
    """A sell's whole shares."""
    reason: str


@dataclass(frozen=True)
class CoreSat:
    equity: tuple[tuple[date, float], ...]
    """Core plus satellite at each day's close, base currency."""
    core: Replay
    satellite: Replay

    @property
    def trades(self) -> tuple[Trade, ...]:
        return tuple(sorted(self.core.trades + self.satellite.trades, key=lambda t: (t.day, t.symbol, t.side)))


def rebalance_days(days: Sequence[date]) -> list[date]:
    """The first of `days` (sorted market days) and the first on or after each yearly anniversary of it."""
    if not days:
        return []
    out, k = [days[0]], 1
    for day in days:
        if day >= add_years(days[0], k * REBALANCE_YEARS):
            out.append(day)
            k += 1
    return out


def add_years(day: date, years: int) -> date:
    year = day.year + years
    return day.replace(year=year, day=min(day.day, calendar.monthrange(year, day.month)[1]))


def rebalance_sells(members: Collection[str], held: Mapping[str, tuple[int, float]], cash: float) -> list[CoreOrder]:
    """`held` is each holding's (shares, value in base) at the last close; `members` the listings
    the core holds equally, each with a current price. A holding not a member goes; one over its
    equal share is trimmed by whole shares."""
    target = _target(members, held, cash)
    out = []
    for symbol, (shares, value) in sorted(held.items()):
        if shares <= 0:
            continue
        if symbol not in members:
            out.append(CoreOrder(symbol, Side.SELL, None, shares, f"{REBALANCE}: no longer in the core"))
        elif value > target and (trim := int((value - target) / (value / shares))) > 0:
            out.append(CoreOrder(symbol, Side.SELL, None, trim, f"{REBALANCE}: trimmed back to an equal share"))
    return out


def rebalance_buys(members: Collection[str], held: Mapping[str, tuple[int, float]], cash: float) -> list[CoreOrder]:
    """Each member under its equal share topped up towards it; together never more than `cash`,
    each scaled down alike when the cash is short."""
    target = _target(members, held, cash)
    wanted = {s: target - held.get(s, (0, 0.0))[1] for s in sorted(members)}
    wanted = {s: v for s, v in wanted.items() if v > 0}
    total = sum(wanted.values())
    if total <= 0 or cash <= 0:
        return []
    scale = min(1.0, cash / total)
    return [CoreOrder(s, Side.BUY, v * scale, None, f"{REBALANCE}: bought towards an equal share")
            for s, v in wanted.items()]


def _target(members: Collection[str], held: Mapping[str, tuple[int, float]], cash: float) -> float:
    equity = cash + sum(v for _, v in held.values())
    return equity / len(members) if members else 0.0


def core_replay(history: Mapping[str, Sequence[Priced]], start: date, end: date, *, capital: float,
                members: Collection[str], slippage: float, commission: float,
                fill_at: Literal["open", "close"] = "open", venues: Mapping[str, Venue] | None = None) -> Replay:
    """The core alone over `history` (as `backtest.replay` takes it): `members` bought in equal
    amounts, set back to equal weight on each `rebalance_days` day, filling like the traded lines
    (the next bar's open or close, with the same costs). Decisions on a day read only its bars."""
    days = sorted({p.bar.date for rows in history.values() for p in rows if start <= p.bar.date <= end})
    due = set(rebalance_days(days))
    seen = dict.fromkeys(history, 0)
    last: dict[str, tuple[date, float]] = {}
    held: dict[str, int] = {}
    cash = capital
    pending: list[tuple[date, CoreOrder]] = []
    buying_next = False
    equity, trades, orders, rejected = [], [], [], []
    for day in days:
        today = {}
        for s, rows in history.items():
            while seen[s] < len(rows) and rows[seen[s]].bar.date <= day:
                seen[s] += 1
            if seen[s] and rows[seen[s] - 1].bar.date == day:
                today[s] = rows[seen[s] - 1]
        waiting = []
        for placed, order in sorted(pending, key=lambda x: x[1].side != Side.SELL):
            p = today.get(order.symbol)
            if p is None or _price(p.bar, fill_at) is None or p.rate is None:
                waiting.append((placed, order))
                continue
            trade = _fill(order, p, _price(p.bar, fill_at), cash, held,
                          _charges(order.symbol, p, venues, slippage, commission))
            if trade is None:
                rejected.append(f"{day.isoformat()} {order.symbol}: the core's buy buys no whole share after costs")
                continue
            cash += trade.cash
            held[trade.symbol] = held.get(trade.symbol, 0) + (trade.shares if trade.side == Side.BUY else -trade.shares)
            if held[trade.symbol] <= 0:
                held.pop(trade.symbol)
            trades.append(trade)
        pending = waiting
        for s, p in today.items():
            if p.rate:
                last[s] = (day, p.bar.close / p.rate)
        equity.append((day, cash + sum(n * last[s][1] for s, n in held.items() if s in last)))
        if day == days[-1]:
            continue
        priced = [s for s in members if s in last and day - last[s][0] <= STALE]
        values = {s: (n, n * last[s][1]) for s, n in held.items() if s in last}
        placed: list[CoreOrder] = []
        if day in due:
            placed = rebalance_sells(priced, values, cash)
            buying_next = bool(placed)
            if not placed:
                placed = rebalance_buys(priced, values, cash)
        elif buying_next and not pending:
            buying_next = False
            placed = rebalance_buys(priced, values, cash)
        for order in placed:
            pending.append((day, order))
            orders.append((day, Decision(order.symbol, Action.BUY if order.side == Side.BUY else Action.SELL,
                                         order.cash, order.reason)))
    return Replay(tuple(equity), tuple(trades), tuple(orders), tuple(rejected),
                  tuple(sorted({o.symbol for _, o in pending})))


def _fill(order: CoreOrder, p: Priced, price: float, cash: float, held: Mapping[str, int], charges) -> Trade | None:
    costs, venue = charges
    if order.side == Side.SELL:
        fill = sell_fill(min(order.shares, held.get(order.symbol, 0)), open_price=price, rate=p.rate,
                         country=venue.country, costs=costs)
    else:
        fill = buy_fill(min(order.cash, cash), open_price=price, rate=p.rate, country=venue.country,
                        exchange=venue.exchange, costs=costs)
        if fill.shares == 0:
            return None
    return Trade(p.bar.date, order.symbol, order.side, fill.shares, fill.price, p.bar.currency, fill.cash,
                 fill_costs(fill, p.rate), order.reason)


def core_sat_replay(history: Mapping[str, Sequence[Priced]], start: date, end: date, *, rules: Rules,
                    risk: Mapping[str, float], members: Collection[str], slippage: float, commission: float,
                    lean: LeanSource, cap: LeanCap | None = None, fill_at: Literal["open", "close"] = "open",
                    venues: Mapping[str, Venue] | None = None) -> CoreSat:
    """`risk["account"]` split CORE_SHARE to the core, the rest to the satellite: `lean` (the
    regime book's, with any extra methods its inputs carry) traded under `rules` within the
    satellite's own cash, its risk sizes and cash floor a share of the satellite alone. The two
    never share cash."""
    capital = risk["account"]
    c = core_replay(history, start, end, capital=capital * CORE_SHARE, members=members, slippage=slippage,
                    commission=commission, fill_at=fill_at, venues=venues)
    s = replay(history, start, end, rules=rules, risk={**risk, "account": capital * (1 - CORE_SHARE)},
               slippage=slippage, commission=commission, lean=lean, fill_at=fill_at, venues=venues, cap=cap)
    sat = dict(s.equity)
    return CoreSat(tuple((d, v + sat[d]) for d, v in c.equity), c, s)
