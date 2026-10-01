"""The live core-sat account's two sleeves. The core holds the main list's companies in equal
amounts, bought once from its first order and never rebalanced; idle core cash (money handed over
when the satellite's share fell) is topped up towards equal shares, never by selling. The satellite
is the regime book traded under the strategy's rules (`regime_service`) on its share of the money,
0 until a signal earns weight (`broker.core_sat.SATELLITE_SHARE`): at 0 it sells what it holds and
buys nothing. Each sleeve's orders carry its own source, so each has its own cash and holdings
inside the one account."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import replace
from datetime import date

from oracle.broker import core_sat
from oracle.broker.core_sat import (
    EXPIRED, SATELLITE_EXIT, SATELLITE_ZERO, STALE, CoreOrder, expired, rebalance_buys, whole_share_buys,
)
from oracle.broker.orders import Action, Side, Status
from oracle.broker.regime import RegimeInputs, RegimeSettings
from oracle.broker.rules import Decision, Lean
from oracle.services.context import AppContext
from oracle.services.errors import UnknownWatchlist
from oracle.services.laya_line import METHOD, LayaInput, with_laya
from oracle.services.paper_service import CORE_SAT, SAT_SOURCE, Sleeve, account_view, place_buy
from oracle.services.prices_service import ConvertedBar
from oracle.services.regime_lines import core_members
from oracle.services.watch_service import DEFAULT_LIST, watchlist_symbols
from oracle.store.paper import DecisionRepo, LoggedDecision, Order, PaperRepo

__all__ = ["CORE_SOURCE", "SAT_SOURCE", "core_card", "core_listings", "core_phase", "core_step", "satellite_exit",
           "satellite_inputs", "satellite_settings", "satellite_share", "satellite_traded", "sleeves"]

CORE_SOURCE = f"{CORE_SAT}:core"
START, TOP_UP = "start", "top up"
TOP_UP_SHARE = 0.05
"""Core cash idle beyond this share of the core's capital (money the satellite handed over) is
topped up towards equal shares; leftovers under a share's price never reach it."""


def satellite_share() -> float:
    return core_sat.SATELLITE_SHARE


def sleeves(ctx: AppContext) -> tuple[Sleeve, Sleeve]:
    """The core's and the satellite's shares of the starting cash."""
    capital, share = ctx.config.risk.account, satellite_share()
    return Sleeve(CORE_SOURCE, capital * (1 - share)), Sleeve(SAT_SOURCE, capital * share)


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


def core_phase(orders: Collection[Order], idle: bool = False) -> str | None:
    """What the core does today, from every order it placed: START with none, TOP_UP when its
    cash is `idle`; None while any order waits or nothing is due. It never rebalances."""
    if any(o.status == Status.PENDING for o in orders):
        return None
    if not orders:
        return START
    return TOP_UP if idle else None


def _spendable(ctx: AppContext, core: Sleeve) -> float:
    """The core's own cash, never more than the account has free: a satellite that sold at a loss
    has spent some of the money its share once held."""
    account = account_view(ctx, CORE_SAT)
    free = account.cash - sum(o.cash for o in account.pending if o.side == Side.BUY)
    return max(0.0, min(account_view(ctx, CORE_SAT, core).cash, free))


def core_step(ctx: AppContext, day: date, history: Mapping[str, list[ConvertedBar]]) -> list[Decision]:
    """The core's orders for today, priced at each listing's last close in `history` up to `day`
    (a close older than STALE leaves it out of this step's targets), logged like every decision."""
    core = sleeves(ctx)[0]
    repo = PaperRepo(ctx.conn)
    today = ctx.clock.utcnow().date()
    orders = _live_orders(repo, today)
    cash = _spendable(ctx, core)
    phase = core_phase(orders, cash >= TOP_UP_SHARE * core.capital)
    if phase is None:
        return []
    closes = {s: rows[-1].close_base for s, rows in history.items()
              if rows and rows[-1].close_base and day - rows[-1].bar.date <= STALE}
    members = [s for s in core_listings(ctx) if s in closes]
    view = account_view(ctx, CORE_SAT, core)
    values = {p.symbol: (p.shares, p.shares * closes[p.symbol]) for p in view.positions if p.symbol in closes}
    planned = whole_share_buys(rebalance_buys(members, values, cash), closes)
    log, out = DecisionRepo(ctx.conn), []
    for o in planned:
        order = place_buy(ctx, o.symbol, o.cash, CORE_SAT, source=CORE_SOURCE)
        d = _decision(o)
        log.log([LoggedDecision(CORE_SAT, today, d.symbol, d.action, d.cash, d.reason, order.id)])
        out.append(d)
    return out


def satellite_exit(ctx: AppContext, day: date) -> dict[str, Lean]:
    """At satellite share 0: its waiting buys are dropped and what it holds leans sell, so its
    money passes to the core."""
    repo = PaperRepo(ctx.conn)
    for o in repo.pending(CORE_SAT):
        if o.source == SAT_SOURCE and o.side == Side.BUY:
            repo.set_status(o.id, Status.REJECTED, SATELLITE_ZERO)
    held = account_view(ctx, CORE_SAT, sleeves(ctx)[1]).positions
    return {p.symbol: Lean(p.symbol, day, "sell", 1.0, CORE_SAT, note=SATELLITE_EXIT) for p in held}


def _live_orders(repo: PaperRepo, today: date) -> list[Order]:
    """The core's orders, a pending one older than STALE dropped first (as the replay drops it): an
    expired order counts as never placed, so an expired first buy is placed again."""
    orders = repo.orders(CORE_SAT, CORE_SOURCE)
    for o in orders:
        if o.status == Status.PENDING and expired(o.placed_on, today):
            repo.set_status(o.id, Status.REJECTED, EXPIRED)
    return [o for o in repo.orders(CORE_SAT, CORE_SOURCE) if o.note != EXPIRED]


def _decision(o: CoreOrder) -> Decision:
    return Decision(o.symbol, Action.BUY if o.side == Side.BUY else Action.SELL, o.cash, o.reason)


def core_card(ctx: AppContext) -> dict:
    """Each sleeve's cash, holdings and positions, and why the satellite holds what it does."""
    core, sat = sleeves(ctx)
    orders = [o for o in PaperRepo(ctx.conn).orders(CORE_SAT, CORE_SOURCE) if o.note != EXPIRED]
    out = {}
    for name, sleeve in (("core", core), ("satellite", sat)):
        view = account_view(ctx, CORE_SAT, sleeve)
        out[name] = {"capital": sleeve.capital, "cash": view.cash, "holdings": view.holdings,
                     "positions": len(view.positions), "pending": len(view.pending)}
    out["core"] |= {"listings": len(core_listings(ctx)), "rebalance": None,
                    "phase": core_phase(orders, _spendable(ctx, core) >= TOP_UP_SHARE * core.capital)}
    out["satellite"] |= {"share": sat.capital / ctx.config.risk.account if ctx.config.risk.account else 0.0,
                         "note": None if sat.capital else SATELLITE_ZERO}
    return out
