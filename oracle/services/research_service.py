"""Research a listing with the ReAct loop, saving every step as it happens."""

from __future__ import annotations

from oracle.research.engine import react
from oracle.research.llm import ChatModel, ModelError
from oracle.research.ollama import OllamaChat
from oracle.research.tools import ToolError
from oracle.research.trace import ResearchRun, RunStatus, Step
from oracle.services.company_service import FIGURES, company_view
from oracle.services.context import AppContext
from oracle.services.errors import ModelUnavailable, ServiceError
from oracle.services.jobs_service import single_flight
from oracle.services.prices_service import price_history
from oracle.services.universe_service import resolve_symbol
from oracle.store.db import to_utc_iso
from oracle.store.research import ResearchRepo
from oracle.store.universe import UniverseRepo

__all__ = ["ResearchRun", "RunStatus", "Step", "latest_research", "research"]

JOB = "research"
# Far longer than a run takes; a crashed holder is freed by the pid check instead.
LEASE_S = 3600.0


async def research(ctx: AppContext, symbol: str, model: ChatModel | None = None) -> ResearchRun:
    """One run over the listing, one at a time across processes. `model` defaults to the
    configured backend."""
    listing = resolve_symbol(ctx, symbol).symbol
    model = model or _configured_model(ctx)
    with single_flight(ctx, JOB, LEASE_S):
        repo = ResearchRepo(ctx.conn)
        run_id = repo.start(listing, model.name, model.model, _now(ctx))
        try:
            outcome = await react(model, [MarketData(ctx, listing)], _task(ctx, listing),
                                  ctx.config.llm.max_steps, lambda s: repo.add_step(run_id, s))
        except ModelError as exc:
            repo.finish(run_id, RunStatus.FAILED, None, str(exc), _now(ctx))
            raise ModelUnavailable(str(exc)) from exc
        repo.finish(run_id, outcome.status, outcome.findings, None, _now(ctx))
        return repo.get(run_id)


def latest_research(ctx: AppContext, symbol: str) -> ResearchRun | None:
    return ResearchRepo(ctx.conn).latest(resolve_symbol(ctx, symbol).symbol)


def _configured_model(ctx: AppContext) -> ChatModel:
    llm = ctx.config.llm
    return OllamaChat(llm.model, llm.base_url, llm.timeout_s, llm.context, transport=ctx.transport)


def _task(ctx: AppContext, listing: str) -> str:
    p = UniverseRepo(ctx.conn).profile(listing)
    where = ", ".join(x for x in (p.exchange, p.country, p.sector) if x)
    return f"Research {p.name} ({listing}){': ' + where if where else ''}."


def _now(ctx: AppContext) -> str:
    return to_utc_iso(ctx.clock.utcnow())


class MarketData:
    """Stored prices and fundamentals only: no fetching inside a research run."""

    name = "market_data"
    description = ("latest price, recent moves, fundamentals and baseline lean of a listing; "
                   "input: its symbol, or empty for the company researched")

    def __init__(self, ctx: AppContext, listing: str) -> None:
        self._ctx = ctx
        self._listing = listing

    async def run(self, input: str) -> str:
        # Small models sometimes leak JSON punctuation into the input.
        symbol = input.strip(" '\"{}[]") or self._listing
        try:
            view = await company_view(self._ctx, symbol, refresh=False)
        except ServiceError as exc:
            raise ToolError(str(exc)) from exc
        p = view.profile
        parts = [f"{p.symbol}: {p.name}, {p.exchange or 'exchange unknown'}, {p.country or 'country unknown'}, "
                 f"{p.sector or 'sector unknown'}."]
        if view.price is None:
            parts.append("No prices stored.")
        else:
            bar = view.price.bar
            parts.append(f"Last close {bar.close:,.2f} {bar.currency} on {bar.date.isoformat()}.")
            bars = price_history(self._ctx, p.symbol, 64)
            for label, back in (("1 day", 1), ("1 month", 21), ("3 months", 63)):
                if len(bars) > back:
                    parts.append(f"Change over {label}: {bars[-1].close / bars[-1 - back].close - 1:+.1%}.")
        f = view.fundamentals
        if f is None:
            parts.append("No fundamentals stored.")
        else:
            known = ", ".join(f"{fig.label} {_figure(fig.kind, v, f.currency)}" for fig in FIGURES
                              if (v := getattr(f, fig.name)) is not None)
            parts.append(f"Fundamentals as of {f.as_of.isoformat()}: {known or 'none known'}.")
        if view.lean is not None:
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
