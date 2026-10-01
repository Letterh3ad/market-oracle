"""The live core-sat account's two sleeves. The core holds the main list's companies in equal
amounts and is set back to equal weight once a year from its first order (`broker.core_sat`: trims
sold first, the laggards topped up from the core's own cash once they have filled); the
satellite is the regime book traded under the strategy's rules (`regime_service`). Each sleeve's
orders carry its own source, so each has its own cash and holdings inside the one account."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import date

from oracle.broker.core_sat import (
    CORE_SHARE, EXPIRED, STALE, CoreOrder, add_years, expired, rebalance_buys, rebalance_sells, whole_share_buys,
)
from oracle.broker.orders import Action, Side, Status
from oracle.broker.regime import RegimeInputs, RegimeSettings
from oracle.broker.rules import Decision
from oracle.services.context import AppContext
from oracle.services.errors import UnknownWatchlist
from oracle.services.laya_line import METHOD, LayaInput, with_laya
from oracle.services.paper_service import CORE_SAT, Sleeve, account_view, place_buy, place_sell
from oracle.services.prices_service import ConvertedBar
from oracle.services.regime_lines import core_members
from oracle.services.watch_service import DEFAULT_LIST, watchlist_symbols
from oracle.store.paper import DecisionRepo, LoggedDecision, Order, PaperRepo

__all__ = ["CORE_SOURCE", "SAT_SOURCE", "core_card", "core_listings", "core_phase", "core_step", "satellite_inputs",
           "satellite_settings", "satellite_traded", "sleeves"]

CORE_SOURCE, SAT_SOURCE = f"{CORE_SAT}:core", f"{CORE_SAT}:satellite"
START, REBALANCE, BUY = "start", "rebalance", "buy"


def sleeves(ctx: AppContext) -> tuple[Sleeve, Sleeve]:
    """The core's and the satellite's shares of the starting cash."""
    capital = ctx.config.risk.account
    return Sleeve(CORE_SOURCE, capital * CORE_SHARE), Sleeve(SAT_SOURCE, capital * (1 - CORE_SHARE))


def core_listings(ctx: AppContext) -> tuple[str, ...]:
    """The main list's companies: never a fund or a never-listed one (Tesla)."""
    try:
        return core_members(ctx, watchlist_symbols(ctx, DEFAULT_LIST)[1])
    except UnknownWatchlist:
        return ()


def satellite_traded(ctx: AppContext) -> list[str]:
    """What the satellite leans on: the core's companies, as in the backtest (never a discovered
    one, a fund or Tesla), plus what it holds, so a holding is never stranded."""
    held = {p.symbol for p in account_view(ctx, CORE_SAT, sleeves(ctx)[1]).positions}
    return sorted(set(core_listings(ctx)) | held)


def satellite_inputs(inputs: RegimeInputs, laya: LayaInput | None = None) -> RegimeInputs:
    """The satellite book's inputs: the regime line's, plus Laya's scores when its verdict gives it
    weight (`laya_line`); at weight 0 the book is the regime line's."""
    if laya is None or not laya.verdict.useful:
        return inputs
    return replace(inputs, extra={**inputs.extra, METHOD: laya.scores})


def satellite_settings(settings: RegimeSettings, laya: LayaInput | None = None) -> RegimeSettings:
    """The regime line's settings, with Laya's row at the weight its verdict gives (0 unless useful)."""
    return with_laya(settings, laya is not None and laya.verdict.useful)


def core_phase(orders: Collection[Order], today: date) -> str | None:
    """What the core does today, from every order it placed: START with none, REBALANCE once a
    year from its first has passed since its last, BUY once a rebalance's trims have all filled;
    None while any order waits or nothing is due."""
    if any(o.status == Status.PENDING for o in orders):
        return None
    if not orders:
        return START
    last = max(o.placed_on for o in orders)
    if last < _anniversary(orders, today):
        return REBALANCE
    batch = [o for o in orders if o.placed_on == last]
    return BUY if all(o.side == Side.SELL for o in batch) else None


def next_rebalance(orders: Collection[Order], today: date) -> date | None:
    if not orders:
        return None
    first = min(o.placed_on for o in orders)
    k = 1
    while add_years(first, k) <= today:
        k += 1
    return add_years(first, k)


def _anniversary(orders: Collection[Order], today: date) -> date:
    """The latest yearly anniversary of the first order on or before today (the first order's day
    in its first year, which no later order is before)."""
    first = min(o.placed_on for o in orders)
    k = 0
    while add_years(first, k + 1) <= today:
        k += 1
    return add_years(first, k)


def core_step(ctx: AppContext, day: date, history: Mapping[str, list[ConvertedBar]]) -> list[Decision]:
    """The core's orders for today, priced at each listing's last close in `history` up to `day`
    (a close older than STALE leaves it out of this step's targets), logged like every decision."""
    core = sleeves(ctx)[0]
    repo = PaperRepo(ctx.conn)
    today = ctx.clock.utcnow().date()
    phase = core_phase(_live_orders(repo, today), today)
    if phase is None:
        return []
    # One stale rule with the replay: a listing without a close in STALE is not counted, trimmed,
    # sold or bought at this step.
    closes = {s: rows[-1].close_base for s, rows in history.items()
              if rows and rows[-1].close_base and day - rows[-1].bar.date <= STALE}
    members = [s for s in core_listings(ctx) if s in closes]
    view = account_view(ctx, CORE_SAT, core)
    values = {p.symbol: (p.shares, p.shares * closes[p.symbol]) for p in view.positions if p.symbol in closes}
    planned = rebalance_sells(members, values, view.cash) if phase == REBALANCE else []
    if not planned:
        planned = whole_share_buys(rebalance_buys(members, values, view.cash), closes)
    log, out = DecisionRepo(ctx.conn), []
    for o in planned:
        order = (place_buy(ctx, o.symbol, o.cash, CORE_SAT, source=CORE_SOURCE) if o.side == Side.BUY
                 else place_sell(ctx, o.symbol, o.shares, CORE_SAT, source=CORE_SOURCE))
        d = _decision(o)
        log.log([LoggedDecision(CORE_SAT, today, d.symbol, d.action, d.cash, d.reason, order.id)])
        out.append(d)
    return out


def _live_orders(repo: PaperRepo, today: date) -> list[Order]:
    """The core's orders, a pending one older than STALE dropped first (as the replay drops it): an
    expired order counts as never placed, so it never blocks a rebalance nor ends the first buying."""
    orders = repo.orders(CORE_SAT, CORE_SOURCE)
    for o in orders:
        if o.status == Status.PENDING and expired(o.placed_on, today):
            repo.set_status(o.id, Status.REJECTED, EXPIRED)
    return [o for o in repo.orders(CORE_SAT, CORE_SOURCE) if o.note != EXPIRED]


def _decision(o: CoreOrder) -> Decision:
    return Decision(o.symbol, Action.BUY if o.side == Side.BUY else Action.SELL, o.cash, o.reason)


def core_card(ctx: AppContext) -> dict:
    """Each sleeve's cash, holdings and positions, and when the core next rebalances."""
    core, sat = sleeves(ctx)
    orders = [o for o in PaperRepo(ctx.conn).orders(CORE_SAT, CORE_SOURCE) if o.note != EXPIRED]
    out = {}
    for name, sleeve in (("core", core), ("satellite", sat)):
        view = account_view(ctx, CORE_SAT, sleeve)
        out[name] = {"capital": sleeve.capital, "cash": view.cash, "holdings": view.holdings,
                     "positions": len(view.positions), "pending": len(view.pending)}
    today = ctx.clock.utcnow().date()
    nxt = next_rebalance(orders, today)
    out["core"] |= {"listings": len(core_listings(ctx)), "next_rebalance": nxt.isoformat() if nxt else None,
                    "phase": core_phase(orders, today)}
    return out
