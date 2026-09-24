"""Tools over Market Oracle's own stored data: no fetching inside a research run."""

from __future__ import annotations

from datetime import date

from oracle.research.tools import ToolError
from oracle.services.company_service import FIGURES, company_view
from oracle.services.context import AppContext
from oracle.services.errors import ServiceError
from oracle.services.peers_service import choose_peers
from oracle.services.prices_service import price_history
from oracle.services.research_tools.shelf import symbol
from oracle.universe.listings import is_receipt

# Trading days of history behind the "3 months" move, plus the last close.
RECENT_BARS = 64
# Enough to see who competes without crowding out the evidence.
MAX_RELATED = 10


async def market_summary(ctx: AppContext, listing: str, as_of: date | None = None) -> str:
    """Stored prices, fundamentals and baseline lean in one paragraph; in an as-of run,
    nothing dated after that day. Raises ServiceError for an unknown listing."""

    def known(day: date) -> bool:
        return as_of is None or day <= as_of

    view = await company_view(ctx, listing, refresh=False)
    p = view.profile
    parts = [f"{p.symbol}: {p.name}, {p.exchange or 'exchange unknown'}, {p.country or 'country unknown'}, "
             f"{p.sector or 'sector unknown'}."]
    bars = [b for b in price_history(ctx, p.symbol) if known(b.date)][-RECENT_BARS:]
    if not bars:
        parts.append("No prices stored.")
    else:
        last = bars[-1]
        parts.append(f"Last close {last.close:,.2f} {last.currency} on {last.date.isoformat()}.")
        for label, back in (("1 day", 1), ("1 month", 21), ("3 months", 63)):
            if len(bars) > back:
                parts.append(f"Change over {label}: {last.close / bars[-1 - back].close - 1:+.1%}.")
    f = view.fundamentals
    if f is None or not known(f.as_of):
        parts.append("No fundamentals stored.")
    else:
        figures = ", ".join(f"{fig.label} {_figure(fig.kind, v, f.currency)}" for fig in FIGURES
                            if (v := getattr(f, fig.name)) is not None)
        parts.append(f"Fundamentals as of {f.as_of.isoformat()}: {figures or 'none known'}.")
    if view.lean is not None and known(view.lean.as_of):
        stale = " (out of date)" if view.lean_stale else ""
        parts.append(f"Baseline lean: {view.lean.label} {view.lean.probability:.0%}{stale}.")
    return " ".join(parts)


def _figure(kind: str, value: float, currency: str | None) -> str:
    if kind == "pct":
        return f"{value:.1%}"
    if kind == "money":
        for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if abs(value) >= size:
                return f"{value / size:.2f}{suffix} {currency or ''}".rstrip()
        return f"{value:,.0f} {currency or ''}".rstrip()
    return f"{value:.2f}"


class MarketData:
    name = "market_data"
    description = ("latest price, recent moves, fundamentals and baseline lean of a listing; "
                   "input: its symbol, or empty for the company researched")

    def __init__(self, ctx: AppContext, listing: str, as_of: date | None = None) -> None:
        self._ctx = ctx
        self._listing = listing
        self._as_of = as_of

    async def run(self, input: str) -> str:
        try:
            return await market_summary(self._ctx, symbol(input, self._listing), self._as_of)
        except ServiceError as exc:
            raise ToolError(str(exc)) from exc


class RelatedCompanies:
    """Peers from the universe: the factor analysts' own choice (same industry group, else
    sector). The relationship graph (phase 8) replaces this behind the same tool."""

    name = "related_companies"
    description = ("companies related to a listing (same industry, else sector), largest first; "
                   "input: its symbol, or empty for the company researched")

    def __init__(self, ctx: AppContext, listing: str) -> None:
        self._ctx = ctx
        self._listing = listing

    async def run(self, input: str) -> str:
        try:
            choice = choose_peers(self._ctx, symbol(input, self._listing))
        except ServiceError as exc:
            raise ToolError(str(exc)) from exc
        if not choice.rows:
            return f"No related companies found for {choice.company.name} ({choice.company.symbol})."
        head = f"{choice.company.name} ({choice.company.symbol}) is related to these by {choice.basis}:"
        # Depositary receipt lines carry their own ISINs, so they repeat a company already
        # listed (Samsung's GDRs); they would crowd out real peers.
        companies = [r for r in choice.rows if not is_receipt(r.name)]
        rows = [f"{r.symbol}: {r.name}" + (f" ({r.market_cap})" if r.market_cap else "")
                for r in companies[:MAX_RELATED]]
        return "\n".join([head, *rows])
