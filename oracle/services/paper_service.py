"""Paper trading: two ledgers in the base currency, orders filled at the next open with
pessimistic costs. `PaperBroker` is the `Broker` a real paper account (phase 12) replaces."""

from __future__ import annotations

from dataclasses import dataclass

from oracle.broker.fills import COSTS, Fill, buy_fill
from oracle.market.fx import pick_rate
from oracle.services.context import AppContext
from oracle.services.prices_service import price_history_in_base
from oracle.services.universe_service import resolve_symbol
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.fx import FxRepo
from oracle.store.paper import FillRow, Order, PaperRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.exchanges import EXCHANGES

__all__ = ["ACCOUNTS", "AccountView", "FillOutcome", "Order", "Position", "account_view", "fill_pending", "place_buy"]

ACCOUNTS = ("strategy", "manual")


@dataclass(frozen=True)
class Position:
    symbol: str
    shares: int
    cost: float
    """What the shares cost in the base currency, costs included (average cost basis)."""
    value: float | None
    """At the last stored close, in the base currency; None without a rate."""


@dataclass(frozen=True)
class AccountView:
    name: str
    cash: float
    positions: tuple[Position, ...]
    pending: tuple[Order, ...]


@dataclass(frozen=True)
class FillOutcome:
    order: Order
    status: str
    """filled, rejected, or pending (waiting, with a note when something is missing)."""
    note: str | None


def place_buy(ctx: AppContext, symbol: str, cash: float, account: str = "manual", source: str = "manual") -> Order:
    """Queue a buy of `cash` (base currency); it fills at the next open."""
    listing = resolve_symbol(ctx, symbol).symbol
    now = ctx.clock.utcnow()
    return PaperRepo(ctx.conn).add_order(account, listing, "buy", cash, None, to_utc_iso(now), now.date(), source)


def fill_pending(ctx: AppContext) -> list[FillOutcome]:
    """Fill every order whose next bar is stored. Orders still waiting for a bar are left
    out; ones blocked by something else are reported with the reason."""
    repo = PaperRepo(ctx.conn)
    out = []
    for order in repo.pending():
        outcome = _try_fill(ctx, repo, order)
        if outcome is not None:
            out.append(outcome)
    return out


def account_view(ctx: AppContext, account: str) -> AccountView:
    repo = PaperRepo(ctx.conn)
    return AccountView(account, _cash(ctx, repo, account), tuple(_positions(ctx, repo, account)),
                       tuple(repo.pending(account)))


def _try_fill(ctx: AppContext, repo: PaperRepo, order: Order) -> FillOutcome | None:
    bar = next((b for b in BarRepo(ctx.conn).history(order.symbol) if b.date > order.placed_on), None)
    if bar is None or bar.open is None:
        return None
    rate = _rate(ctx, bar.currency, bar.date)
    if rate is None:
        note = f"no {bar.currency} rate for {bar.date.isoformat()}: run `oracle fx update`"
        repo.set_status(order.id, "pending", note)
        return FillOutcome(order, "pending", note)
    profile = UniverseRepo(ctx.conn).profile(order.symbol)
    country = EXCHANGES.get(profile.exchange or "", (profile.country or "", 0))[0]
    cash = _cash(ctx, repo, order.account)
    if order.cash > cash:
        return _reject(repo, order, f"not enough cash: {order.cash:,.2f} asked, {cash:,.2f} available")
    fill = buy_fill(order.cash, open_price=bar.open, rate=rate, country=country, exchange=profile.exchange or "",
                    costs=COSTS)
    if fill.shares == 0:
        return _reject(repo, order, f"{order.cash:,.2f} buys no whole share at {bar.open:,.2f} {bar.currency} after costs")
    repo.record_fill(_row(order, bar.date, bar.currency, rate, fill))
    return FillOutcome(order, "filled", None)


def _reject(repo: PaperRepo, order: Order, note: str) -> FillOutcome:
    repo.set_status(order.id, "rejected", note)
    return FillOutcome(order, "rejected", note)


def _row(order: Order, day, currency: str, rate: float, fill: Fill) -> FillRow:
    return FillRow(order.id, order.account, order.symbol, order.side, day, fill.shares, fill.price, currency, rate,
                   fill.cash, fill.fee, fill.stamp, fill.fx, fill.slippage)


def _rate(ctx: AppContext, currency: str, day) -> float | None:
    if currency == ctx.config.base_currency:
        return 1.0
    picked = pick_rate(FxRepo(ctx.conn, ctx.config.base_currency).history(currency), day)
    return picked.units_per_base if picked else None


def _cash(ctx: AppContext, repo: PaperRepo, account: str) -> float:
    return ctx.config.risk.account + sum(f.cash for f in repo.fills(account))


def _positions(ctx: AppContext, repo: PaperRepo, account: str) -> list[Position]:
    held: dict[str, list[float]] = {}  # symbol -> [shares, cost]
    for f in repo.fills(account):
        shares, cost = held.get(f.symbol, [0, 0.0])
        if f.side == "buy":
            held[f.symbol] = [shares + f.shares, cost - f.cash]
        else:
            held[f.symbol] = [shares - f.shares, cost * (shares - f.shares) / shares]
    out = []
    for symbol, (shares, cost) in sorted(held.items()):
        if shares:
            last = price_history_in_base(ctx, symbol, days=1)
            value = shares * last[-1].close_base if last and last[-1].close_base is not None else None
            out.append(Position(symbol, int(shares), cost, value))
    return out
