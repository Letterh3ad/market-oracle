"""Total return (boring-plan spec 1): the one seam every buy-and-hold, index and benchmark line
reads, each listing's stored closes with its stored cash dividends reinvested at the ex-date."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from oracle.broker.dividends import Payout
from oracle.broker.total_return import reinvest_factors
from oracle.services.context import AppContext
from oracle.services.prices_service import ConvertedBar, benchmark_history_in_base
from oracle.store.dividends import DividendRepo

__all__ = ["PRICE_ONLY", "REINVESTED", "TOTAL_RETURN", "TotalReturn", "held_kind", "held_note", "payouts",
           "reinvested", "total_return_closes", "traded_payouts"]

TOTAL_RETURN = "total return"
PRICE_ONLY = "price only"
PARTLY = "partly price only"
REINVESTED = "dividends reinvested at each ex-date's close"


@dataclass(frozen=True)
class TotalReturn:
    closes: list[tuple[date, float | None]]
    """Each close times the shares one first-day share has grown to; None where the close is
    (no rate)."""
    covered: bool
    """Dividends were asked for over every day here; otherwise the closes are price only."""
    dividends: int
    """How many were reinvested."""

    @property
    def kind(self) -> str:
        return TOTAL_RETURN if self.covered else PRICE_ONLY


def reinvested(ctx: AppContext, symbol: str, rows: Sequence[ConvertedBar], local: bool = False) -> TotalReturn:
    """`rows` (one listing's bars, oldest first) with its dividends reinvested: in the base
    currency, or with `local` in the listing's own. Price only when its dividends were never
    asked for over all of `rows`."""
    if not rows:
        return TotalReturn([], False, 0)
    paid = payouts(ctx, symbol, rows)
    found = [(p.ex_date, p.amount) for p in paid or () if p.ex_date > rows[0].bar.date]
    covered = paid is not None
    factors = reinvest_factors([(r.bar.date, r.bar.close) for r in rows], found)
    closes = [(r.bar.date, r.bar.close * factors[r.bar.date] if local
               else None if r.close_base is None else r.close_base * factors[r.bar.date]) for r in rows]
    return TotalReturn(closes, covered, len(found))


def payouts(ctx: AppContext, symbol: str, rows: Sequence[ConvertedBar]) -> list[Payout] | None:
    """The listing's stored dividends with an ex-date inside `rows` (in the bars' currency), or
    None when its dividends were never asked for over all of `rows`."""
    if not rows:
        return None
    first, last = rows[0].bar.date, rows[-1].bar.date
    repo = DividendRepo(ctx.conn)
    span = repo.span(symbol)
    if span is None or span[0] > first or span[1] < last:
        return None
    currency = rows[-1].bar.currency
    return [Payout(d.ex_date, d.amount, d.currency, d.pay_date) for d in repo.history(symbol, first, last)
            if d.currency == currency]


def traded_payouts(ctx: AppContext, history: Mapping[str, Sequence[ConvertedBar]]
                   ) -> tuple[dict[str, list[Payout]], list[str]]:
    """Each listing's dividends for the traded lines, and the listings whose dividends were never
    fetched (they trade price only)."""
    out, short = {}, []
    for s, rows in history.items():
        paid = payouts(ctx, s, rows)
        if paid is None:
            short.append(s)
        else:
            out[s] = paid
    return out, sorted(short)


def total_return_closes(ctx: AppContext, symbol: str, end: date | None = None, local: bool = False) -> TotalReturn:
    """A listing's stored closes to `end` with its dividends reinvested, read by symbol (a
    benchmark need not be in the universe)."""
    rows = [c for c in benchmark_history_in_base(ctx, symbol) if end is None or c.bar.date <= end]
    return reinvested(ctx, symbol, rows, local)


def held_kind(returned: Mapping[str, TotalReturn]) -> str:
    """A line holding several listings: total return, partly price only, or price only."""
    covered = [r.covered for r in returned.values()]
    return TOTAL_RETURN if all(covered) else PRICE_ONLY if not any(covered) else PARTLY


def held_note(returned: Mapping[str, TotalReturn]) -> str:
    """How a held line's listings count dividends, naming any not fetched yet."""
    short = sorted(s for s, r in returned.items() if not r.covered)
    if not short:
        return REINVESTED
    if len(short) == len(returned):
        return f"{PRICE_ONLY} (dividends not fetched yet)"
    return f"{REINVESTED}, except price only for {', '.join(short)} (dividends not fetched yet)"
