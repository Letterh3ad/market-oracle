"""Research a listing with the ReAct loop, saving every step as it happens."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from oracle.net.client import RateLimitedClient
from oracle.net.gate import SourceGate
from oracle.research.citations import keep_citations
from oracle.research.engine import react
from oracle.research.llm import ChatModel, ModelError
from oracle.research.ollama import OllamaChat
from oracle.research.news import Article
from oracle.research.queries import plan, seed_queries, suggest
from oracle.research.roles import write_report
from oracle.research.tools import Tool
from oracle.research.trace import UNVALIDATED, Claim, Report, ResearchRun, RunStatus, Step
from oracle.research.web import DdgsSearcher, WebSearcher
from oracle.services.analysis_service import latest_signals
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, ModelUnavailable
from oracle.services.http import open_client
from oracle.services.jobs_service import single_flight
from oracle.services.research_tools import (
    Filings, GdeltSearch, MarketData, NewsSearch, ReadPage, RelatedCompanies, Shelf, WebSearch,
)
from oracle.services.universe_service import resolve_symbol
from oracle.store.db import to_utc_iso
from oracle.store.research import ResearchRepo
from oracle.store.universe import ListingProfile, UniverseRepo

__all__ = [
    "UNVALIDATED", "Claim", "Report", "ResearchRun", "RunStatus", "Step", "latest_research", "report_history", "research",
]

JOB = "research"
# Far longer than a run takes; a crashed holder is freed by the pid check instead.
LEASE_S = 3600.0


async def research(ctx: AppContext, symbol: str, model: ChatModel | None = None,
                   as_of: date | None = None, searcher: WebSearcher | None = None) -> ResearchRun:
    """One run over the listing, one at a time across processes. `model` defaults to the
    configured backend. With `as_of`, the run replays that day: every tool sees only what
    existed by its end, for backtests after the model's training cutoff."""
    listing = resolve_symbol(ctx, symbol).symbol
    today = ctx.clock.utcnow().date()
    if as_of and as_of > today:
        raise InvalidQuery(f"as-of date {as_of.isoformat()} is in the future (today is {today.isoformat()})")
    model = model or _configured_model(ctx)
    with single_flight(ctx, JOB, LEASE_S):
        repo = ResearchRepo(ctx.conn)
        run_id = repo.start(listing, model.name, model.model, _now(ctx), as_of)
        shown: set[int] = set()

        def on_shown(ids: list[int]) -> None:
            shown.update(ids)
            repo.add_evidence(run_id, ids)

        day = as_of or today
        profile = UniverseRepo(ctx.conn).profile(listing)
        try:
            queries = plan(seed_queries(profile.name, profile.country),
                           await suggest(model, _about(profile, day), profile.country))
            repo.set_queries(run_id, queries)
            shelf = Shelf(ctx.conn, ctx.clock, on_shown, as_of)
            async with open_client(ctx) as client:
                tools = _tools(ctx, client, shelf, listing, as_of, searcher)
                outcome = await react(model, tools, _task(profile, day, queries), ctx.config.llm.max_steps,
                                      lambda s: repo.add_step(run_id, s))
            findings, cited = keep_citations(outcome.findings, shown)
            evidence = await _evidence(ctx, shelf, repo, run_id, listing, as_of, day, findings)
            report = await write_report(model, evidence, shown, day)
        except ModelError as exc:
            repo.finish(run_id, RunStatus.FAILED, None, str(exc), _now(ctx))
            raise ModelUnavailable(str(exc)) from exc
        repo.save_report(run_id, listing, report)
        repo.set_cited(run_id, cited | report.cites)
        repo.finish(run_id, outcome.status, findings, None, _now(ctx))
        return repo.get(run_id)


async def _evidence(ctx: AppContext, shelf: Shelf, repo: ResearchRepo, run_id: int, listing: str,
                    as_of: date | None, day: date, notes: str) -> str:
    """What the roles read: the stored data as one citable item, every article the run was
    shown (newest first), and the loop's own notes."""
    signals = "; ".join(f"{s.signal.analyst}: {s.signal.direction.value} ({s.signal.reason})"
                        + (" [out of date]" if s.stale else "")
                        for s in latest_signals(ctx, listing) if s.signal.as_of <= day)
    data = await MarketData(ctx, listing, as_of).run("") + (f" Analyst signals: {signals}." if signals else "")
    [data_id] = shelf.keep([Article(f"oracle:data/{listing}/{day.isoformat()}",
                                    f"{listing} stored prices, fundamentals and analyst signals", "stored data",
                                    datetime.combine(day, time(), timezone.utc))], "oracle")
    lines = [f"[a{data_id}] {day.isoformat()} stored data: {data}"]
    for e in repo.get(run_id).evidence:
        if e.id != data_id:
            lines.append(f"[a{e.id}] {e.published.date().isoformat() if e.published else 'undated'} "
                         f"{e.outlet or 'unknown outlet'}: {e.title}")
    return "Evidence:\n" + "\n".join(lines) + f"\n\nResearcher's notes: {notes or '(none)'}"


def report_history(ctx: AppContext, symbol: str) -> list[ResearchRun]:
    """Every run with a report for the listing, newest first."""
    return ResearchRepo(ctx.conn).with_reports(resolve_symbol(ctx, symbol).symbol)


def _tools(ctx: AppContext, client: RateLimitedClient, shelf: Shelf, listing: str, as_of: date | None,
           searcher: WebSearcher | None) -> list[Tool]:
    contact = ctx.config.research.contact
    tools = [MarketData(ctx, listing, as_of), NewsSearch(client, shelf), GdeltSearch(client, shelf),
             Filings(client, ctx.conn, ctx.clock, shelf, listing, contact), RelatedCompanies(ctx, listing)]
    if as_of is None:
        # Web pages carry no reliable date, so a replay cannot use them.
        gate = SourceGate(ctx.conn, ctx.clock, ctx.config.sources)
        tools += [WebSearch(searcher or DdgsSearcher(), gate, shelf), ReadPage(client, shelf, contact)]
    return tools


def latest_research(ctx: AppContext, symbol: str) -> ResearchRun | None:
    return ResearchRepo(ctx.conn).latest(resolve_symbol(ctx, symbol).symbol)


def _configured_model(ctx: AppContext) -> ChatModel:
    llm = ctx.config.llm
    return OllamaChat(llm.model, llm.base_url, llm.timeout_s, llm.context, transport=ctx.transport)


def _about(p: ListingProfile, day: date) -> str:
    # The date matters twice: "recent" needs an anchor, and a replay must not lean on what
    # the model remembers from after it.
    where = ", ".join(x for x in (p.exchange, p.country, p.sector, p.industry) if x)
    return (f"Today is {day.isoformat()}; use nothing you know from after it. "
            f"Company: {p.name} ({p.symbol}){': ' + where if where else ''}.")


def _task(p: ListingProfile, day: date, queries: list[str]) -> str:
    searches = "\n".join(f"- {q}" for q in queries)
    return f"{_about(p, day)}\nResearch it. Start with these searches, then follow what you find:\n{searches}"


def _now(ctx: AppContext) -> str:
    return to_utc_iso(ctx.clock.utcnow())
