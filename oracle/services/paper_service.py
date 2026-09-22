"""Paper trading: two ledgers in the base currency, orders filled at the next open with
pessimistic costs."""

from __future__ import annotations

from dataclasses import dataclass

from oracle.analysts.risk import max_drawdown
from oracle.broker.fills import COSTS, Fill, buy_fill, sell_fill
from oracle.market.fx import pick_rate
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.prices_service import price_history_in_base
from oracle.services.universe_service import resolve_symbol
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.fx import FxRepo
from oracle.store.paper import EquityRepo, FillRow, Order, PaperRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.exchanges import EXCHANGES

__all__ = [
    "ACCOUNTS", "AccountScore", "AccountView", "FillOutcome", "Order", "Position", "account_score", "account_view",
    "equity_history", "fill_pending", "place_buy", "place_sell",
]

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
    realised: float
    """Profit or loss locked in by sells, in the base currency, after all costs."""


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


def place_sell(ctx: AppContext, symbol: str, shares: int | None, account: str = "manual",
               source: str = "manual") -> Order:
    """Queue a sell of `shares` (None: the whole position); long only, so never more than
    held and not already queued for sale."""
    listing = resolve_symbol(ctx, symbol).symbol
    repo = PaperRepo(ctx.conn)
    held = _replay(repo, account)[0].get(listing, [0, 0.0])[0]
    if not held:
        raise InvalidQuery(f"{listing} is not held on the {account} account")
    queued = [o for o in repo.pending(account) if o.symbol == listing and o.side == "sell"]
    free = 0 if any(o.shares is None for o in queued) else held - sum(o.shares for o in queued)
    if free <= 0:
        raise InvalidQuery(f"all {held} {listing} shares already have a pending sell")
    if shares is not None and not 0 < shares <= free:
        raise InvalidQuery(f"can sell 1 to {free} {listing} shares, not {shares}")
    now = ctx.clock.utcnow()
    return repo.add_order(account, listing, "sell", None, shares,
                          to_utc_iso(now), now.date(), source)


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
    holdings = _holdings(view)
    unrealised = sum(p.value - p.cost for p in view.positions if p.value is not None)
    costs = sum((f.fee + f.stamp) / f.rate + f.fx + f.slippage for f in fills)
    history = EquityRepo(ctx.conn).history(account)
    max_fall = max_drawdown(history)[0] if history else 0.0
    return AccountScore(account, view.cash + holdings, ctx.config.risk.account, view.realised, unrealised, costs,
                        max_fall)


def equity_history(ctx: AppContext, account: str) -> list[tuple]:
    """(date, equity) per day a fill run happened, oldest first."""
    return EquityRepo(ctx.conn).history(account)


def _record_equity(ctx: AppContext) -> None:
    today = ctx.clock.utcnow().date()
    for account in ACCOUNTS:
        view = account_view(ctx, account)
        EquityRepo(ctx.conn).record(account, today, view.cash, _holdings(view))


def _holdings(view: AccountView) -> float:
    """Positions at their last close; one without a rate yet counts as nothing."""
    return sum(p.value for p in view.positions if p.value is not None)


def account_view(ctx: AppContext, account: str) -> AccountView:
    repo = PaperRepo(ctx.conn)
    held, realised = _replay(repo, account)
    return AccountView(account, _cash(ctx, repo, account), tuple(_positions(ctx, held)), tuple(repo.pending(account)),
                       realised)


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
    if order.side == "sell":
        held = _replay(repo, order.account)[0].get(order.symbol, [0, 0.0])[0]
        shares = order.shares if order.shares is not None else held
        if not 0 < shares <= held:
            return _reject(repo, order, f"only {held} shares held")
        fill = sell_fill(shares, open_price=bar.open, rate=rate, country=country, costs=COSTS)
        repo.record_fill(_row(order, bar.date, bar.currency, rate, fill))
        return FillOutcome(order, "filled", None)
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


def _replay(repo: PaperRepo, account: str) -> tuple[dict[str, list], float]:
    """({symbol: [shares, cost]}, realised) from the account's fills, at average cost."""
    held: dict[str, list] = {}
    realised = 0.0
    for f in repo.fills(account):
        shares, cost = held.get(f.symbol, [0, 0.0])
        if f.side == "buy":
            held[f.symbol] = [shares + f.shares, cost - f.cash]
        else:
            sold_cost = cost * f.shares / shares
            realised += f.cash - sold_cost
            held[f.symbol] = [shares - f.shares, cost - sold_cost]
    return held, realised


def _positions(ctx: AppContext, held: dict[str, list]) -> list[Position]:
    out = []
    for symbol, (shares, cost) in sorted(held.items()):
        if shares:
            last = price_history_in_base(ctx, symbol, days=1)
            value = shares * last[-1].close_base if last and last[-1].close_base is not None else None
            out.append(Position(symbol, int(shares), cost, value))
    return out
