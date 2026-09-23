"""Research a listing with the ReAct loop, saving every step as it happens."""

from __future__ import annotations

from oracle.research.citations import keep_citations
from oracle.research.engine import react
from oracle.research.llm import ChatModel, ModelError
from oracle.research.ollama import OllamaChat
from oracle.research.trace import ResearchRun, RunStatus, Step
from oracle.services.context import AppContext
from oracle.services.errors import ModelUnavailable
from oracle.services.jobs_service import single_flight
from oracle.services.research_tools import MarketData, NewsSearch
from oracle.services.http import open_client
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
        shown: set[int] = set()

        def on_shown(ids: list[int]) -> None:
            shown.update(ids)
            repo.add_evidence(run_id, ids)

        async with open_client(ctx) as client:
            tools = [MarketData(ctx, listing), NewsSearch(client, ctx.conn, ctx.clock, on_shown)]
            try:
                outcome = await react(model, tools, _task(ctx, listing), ctx.config.llm.max_steps,
                                      lambda s: repo.add_step(run_id, s))
            except ModelError as exc:
                repo.finish(run_id, RunStatus.FAILED, None, str(exc), _now(ctx))
                raise ModelUnavailable(str(exc)) from exc
        findings, cited = keep_citations(outcome.findings, shown)
        repo.set_cited(run_id, cited)
        repo.finish(run_id, outcome.status, findings, None, _now(ctx))
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
