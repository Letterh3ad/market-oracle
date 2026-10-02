"""The broad index as the honest "do nothing" (next-signals spec 4): the live `index` paper account
puts all its cash in ACWI once and holds it, and every backtest, walk-forward and replay-study table
leads with ACWI and SPY bought and held from its first day, dividends reinvested (total return)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from oracle.broker.compare import INDEX_LINES, INDEX_SYMBOL, buy_and_hold
from oracle.broker.orders import Action
from oracle.broker.rules import Decision
from oracle.services.context import AppContext
from oracle.services.dividends_service import PRICE_ONLY, TotalReturn, total_return_closes
from oracle.services.paper_service import INDEX, account_view, place_buy
from oracle.services.prices_service import update_listing
from oracle.store.bars import BarRepo
from oracle.store.paper import DecisionRepo, LoggedDecision, PaperRepo
from oracle.store.universe import UniverseRepo

__all__ = ["BUY_ALL", "INDEX_LINES", "NO_BARS", "index_caption", "NOT_LISTED", "index_closes", "index_equity", "index_label",
           "index_missing",
           "index_step", "update_index_prices"]

BUY_ALL = f"buy and hold: all the cash in {INDEX_SYMBOL} at the next open, then held, never sold"
NO_BARS = (f"; no {INDEX_SYMBOL} prices stored yet: the next price update fetches them (an ordered listing is "
           "priced) and the order fills at the first open after it was placed")
NOT_LISTED = f"{INDEX_SYMBOL} is not in the universe (or it is not loaded yet): refresh the universe"


def index_step(ctx: AppContext) -> list[Decision]:
    """The index account's step: with nothing held or waiting, a buy of ACWI with all its cash;
    otherwise nothing (it holds). Logged like every decision."""
    if PaperRepo(ctx.conn).pending(INDEX):
        return []
    view = account_view(ctx, INDEX)
    if any(p.symbol == INDEX_SYMBOL for p in view.positions) or view.cash <= 0:
        return []
    today = ctx.clock.utcnow().date()
    if UniverseRepo(ctx.conn).resolve(INDEX_SYMBOL) is None:
        d = Decision(INDEX_SYMBOL, Action.SKIP, None, NOT_LISTED)
        DecisionRepo(ctx.conn).log([LoggedDecision(INDEX, today, d.symbol, d.action, None, d.reason, None)])
        return [d]
    order = place_buy(ctx, INDEX_SYMBOL, view.cash, INDEX, source=INDEX)
    reason = BUY_ALL + ("" if BarRepo(ctx.conn).last_date(INDEX_SYMBOL) else NO_BARS)
    d = Decision(INDEX_SYMBOL, Action.BUY, order.cash, reason)
    DecisionRepo(ctx.conn).log([LoggedDecision(INDEX, today, d.symbol, d.action, d.cash, d.reason, order.id)])
    return [d]


async def update_index_prices(ctx: AppContext) -> str:
    """ACWI's and SPY's bars, for the backtest's index lines, unless the price update already
    fetched them (held, ordered or watched); a failure is said, never raised."""
    priced = set(PaperRepo(ctx.conn).held_symbols()) | {o.symbol for o in PaperRepo(ctx.conn).pending()}
    universe, done = UniverseRepo(ctx.conn), []
    for _, symbol, _ in INDEX_LINES:
        if symbol in priced or universe.resolve(symbol) is None:
            continue
        try:
            r = await update_listing(ctx, symbol)
        except Exception as exc:  # the index lines wait for the next tick; the tick's prices still count
            done.append(f"{symbol} failed: {exc}")
        else:
            done.append(f"{symbol} {r.added} new bars")
    return "; index: " + ", ".join(done) if done else ""


def index_closes(ctx: AppContext, end: date, local: bool = False) -> dict[str, TotalReturn]:
    """Each index line's stored closes up to `end` with dividends reinvested: in the base currency,
    or with `local` in the fund's own (as the replay study holds each listing's rate at its first day)."""
    return {symbol: total_return_closes(ctx, symbol, end, local) for _, symbol, _ in INDEX_LINES}


def index_equity(closes: Mapping[str, TotalReturn], symbol: str, first: date, last: date,
                 capital: float) -> list[tuple[date, float]]:
    """`capital` in `symbol` at its first close from `first`, held to `last`, no costs."""
    rows = closes[symbol].closes if symbol in closes else []
    return buy_and_hold({symbol: [(d, c) for d, c in rows if d <= last]}, first, capital)[0]


def index_label(label: str, closes: Mapping[str, TotalReturn], symbol: str) -> str:
    """The line's name and whether it is total return or (dividends never fetched) price only."""
    return f"{label} {closes[symbol].kind if symbol in closes else PRICE_ONLY}"


def index_caption(symbol: str, label: str, start: date, closes: Mapping[str, TotalReturn]) -> str:
    kind = (f"Total return: dividends reinvested at each ex-date's close ({closes[symbol].dividends} in its "
            "stored prices)." if symbol in closes and closes[symbol].covered
            else f"Price only: {symbol}'s dividends are not fetched yet (the next price update fetches them).")
    return (f"{label}: all the capital in {symbol} at its first close from {start.isoformat()} and held, no costs: "
            f"the honest \"do nothing\" every line is measured against. {kind}")


def index_missing(closes: Mapping[str, TotalReturn], start: date) -> list[str]:
    """A note per index line with no stored close from `start`."""
    return [f"No stored {symbol} prices from {start.isoformat()}: the {label} line is empty until a tick's price "
            "update fetches them." for _, symbol, label in INDEX_LINES
            if not any(d >= start and c is not None for d, c in (closes[symbol].closes if symbol in closes else ()))]
