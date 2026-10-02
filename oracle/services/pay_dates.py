"""Pay dates for the dividends a paper ledger may be owed, so each is credited on the day it is
paid. Only Nasdaq gives them, for US listings; any other waits the assumed lag."""

from __future__ import annotations

from datetime import date, timedelta

from oracle.market import nasdaq
from oracle.market.sources import NoData, SourceFailed
from oracle.net.client import RateLimitedClient
from oracle.services.context import AppContext
from oracle.store.dividends import DividendRepo
from oracle.store.paper import PaperRepo

ASK_FOR = timedelta(days=90)
"""Dividends with an ex-date this recent are looked up (the paper ledgers' assumed pay lag)."""


def us_listing(symbol: str, currency: str) -> bool:
    """Nasdaq lists US exchange tickers: dollar lines without a Yahoo exchange suffix."""
    return currency == "USD" and not any(c in symbol for c in ".=^")


async def find_pay_dates(ctx: AppContext, client: RateLimitedClient, today: date) -> list[str]:
    """Ask Nasdaq, at most once a day per listing, for the pay dates of recent dividends still
    without one on listings a paper ledger has traded; a failure is said, never raised."""
    repo, notes = DividendRepo(ctx.conn), []
    for symbol in PaperRepo(ctx.conn).traded_symbols():
        unpaid = [d for d in repo.unpaid(symbol, today - ASK_FOR) if us_listing(symbol, d.currency)]
        if not unpaid or repo.checked_on(symbol, today):
            continue
        try:
            found = await nasdaq.pay_dates(client, symbol)
        except (NoData, SourceFailed, KeyError, TypeError, ValueError, AttributeError) as exc:
            notes.append(f"{symbol} pay dates: {exc or type(exc).__name__}")
        else:
            repo.set_pay_dates(symbol, {d.ex_date: found[d.ex_date] for d in unpaid if d.ex_date in found})
        repo.mark_checked(symbol, today)
    return notes
