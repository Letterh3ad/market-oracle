"""Research a listing with the ReAct loop, saving every step as it happens."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from oracle.net.client import RateLimitedClient
from oracle.net.gate import SourceGate
from oracle.research.citations import keep_citations
from oracle.research.engine import react
from oracle.research.freshness import Known, due_for_research
from oracle.research.llm import ChatModel, ModelError
from oracle.research.news import Article
from oracle.research.queries import EDITION, MAX_LINKED, Search, link_queries, plan, seed_queries, suggest
from oracle.research.roles import digest, digest_room, write_report
from oracle.research.tools import Tool
from oracle.research.trace import (
    UNVALIDATED, UNVALIDATED_TAG, Claim, Report, ResearchRun, RunStatus, StageModel, Step, Verdict,
)
from oracle.research.verdict import Material, add_verdict
from oracle.research.web import DdgsSearcher, WebSearcher
from oracle.services.analysis_service import latest_signals
from oracle.services.context import AppContext
from oracle.services.errors import (
    InvalidQuery, JobAlreadyRunning, ModelUnavailable, ServiceError, UnknownResearchRun,
)
from oracle.services.http import open_client
from oracle.services.jobs_service import job_statuses, single_flight
from oracle.services.links_service import research_links
from oracle.services.models_service import RESEARCH_STAGES, RunModel, resolve_stages
from oracle.services.research_tools import (
    Filings, GdeltSearch, LocalNewsSearch, MarketData, NewsSearch, ReadPage, Reader, RelatedCompanies, Shelf, WebSearch,
    evidence_entry, market_summary,
)
from oracle.services.universe_service import resolve_symbol
from oracle.services.watch_service import discovered_only
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso, transaction
from oracle.store.job_history import INTERRUPTED
from oracle.store.research import ResearchRepo
from oracle.store.universe import ListingProfile, UniverseRepo
from oracle.store.watchlists import WatchlistRepo
from oracle.system import idle

__all__ = [
    "UNVALIDATED", "UNVALIDATED_TAG", "Claim", "check_as_of", "Refresh", "Report", "ResearchRun", "RunStatus", "Step", "Verdict",
    "latest_research", "latest_verdict", "pc_in_use", "refresh_research", "report_history", "research", "research_progress", "research_run", "stale_research",
    "sweep_interrupted",
]

log = logging.getLogger(__name__)

JOB = "research"
UNUSABLE = "the model's answers were unusable"
# Far longer than a run takes; a crashed holder is freed by the pid check instead.
LEASE_S = 3600.0


async def research(ctx: AppContext, symbol: str, model: ChatModel | None = None,
                   as_of: date | None = None, searcher: WebSearcher | None = None,
                   profile: str | None = None) -> ResearchRun:
    """One run over the listing, one at a time across processes. Each stage runs on its slot's
    profile, or every stage on `profile` (a run's picked model); `model` (a test's) answers
    every stage. With `as_of`, the run replays that day: every tool sees only what existed by
    its end, for backtests after the model's training cutoff."""
    listing = resolve_symbol(ctx, symbol).symbol
    check_as_of(ctx, as_of)
    today = ctx.clock.utcnow().date()
    stages = _Stages(resolve_stages(ctx, RESEARCH_STAGES, profile, model, lead="research"))
    with single_flight(ctx, JOB, LEASE_S):
        repo = ResearchRepo(ctx.conn)
        lead = stages.peek("research")
        run_id = repo.start(listing, lead.name, lead.model, _now(ctx), as_of, stages.records())
        try:
            await _run(ctx, repo, run_id, listing, stages, as_of or today, as_of, searcher)
        except BaseException as exc:
            _record_failure(ctx, repo, run_id, stages, exc)
            if isinstance(exc, ModelError):
                unavailable = ModelUnavailable(str(exc))
                for note in getattr(exc, "__notes__", ()):
                    unavailable.add_note(note)
                raise unavailable from exc
            raise
        return repo.get(run_id)


class _Stages:
    """Each stage's model and context, and what each stage's model fell back to. Stages run
    one after another, so a model shared by two stages counts each fallback for the stage
    that was running."""

    def __init__(self, resolved: dict[str, RunModel]) -> None:
        self._resolved = resolved
        self._fell: dict[str, set[str]] = {
            stage: {f"{r.profile} profile ({r.unavailable})"} if r.unavailable else set() for stage, r in resolved.items()}
        self._current: str | None = None

    def peek(self, stage: str) -> ChatModel:
        return self._resolved[stage].model

    def context(self, stage: str) -> int:
        return self._resolved[stage].context

    def model(self, stage: str) -> ChatModel:
        """The stage's model, from now on counting its fallbacks as this stage's."""
        self._settle()
        model = self._resolved[stage].model
        model.fell_back_to.clear()
        self._current = stage
        return model

    def _settle(self) -> None:
        if self._current:
            self._fell[self._current] |= self._resolved[self._current].model.fell_back_to

    def records(self) -> tuple[StageModel, ...]:
        self._settle()
        self._current = None
        return tuple(StageModel(stage, r.profile, r.model.name, r.model.model, tuple(sorted(self._fell[stage])))
                     for stage, r in self._resolved.items())


async def _run(ctx: AppContext, repo: ResearchRepo, run_id: int, listing: str, stages: _Stages,
               day: date, as_of: date | None, searcher: WebSearcher | None) -> None:
    shown: set[int] = set()

    def on_shown(ids: list[int], query: str | None) -> None:
        shown.update(ids)
        repo.add_evidence(run_id, ids, query)

    profile = UniverseRepo(ctx.conn).profile(listing)
    # Web pages carry no reliable date, so a replay cannot search the web.
    searcher = None if as_of else searcher or DdgsSearcher()
    linked = [(x.name or x.symbol, x.label) for x in research_links(ctx, listing, MAX_LINKED, as_of)]
    seeds = seed_queries(profile.name, profile.country, searcher is not None) + link_queries(profile.name, linked)
    queries = plan(seeds, await suggest(stages.model("queries"), _about(profile, day), profile.country),
                   profile.country)
    repo.set_queries(run_id, [(q.query, q.via) for q in queries])
    shelf = Shelf(ctx.conn, ctx.clock, on_shown, as_of)
    contact = ctx.config.research.contact
    gate = SourceGate(ctx.conn, ctx.clock, ctx.config.sources)
    async with open_client(ctx) as client:
        # SEC refuses requests without a contact; offering the tool would only waste a step.
        sec = Filings(client, ctx.conn, ctx.clock, shelf, listing, contact) if contact else None
        tools = _tools(ctx, client, shelf, listing, profile.country, as_of, searcher, gate, sec)
        outcome = await react(stages.model("research"), tools, _task(profile, day, queries),
                              ctx.config.llm.max_steps, lambda s: repo.add_step(run_id, s))
        reader = Reader(ctx.conn, ctx.clock, client, searcher, gate, contact, stages.model("notes"),
                        f"{profile.name} ({profile.symbol})", stages.context("notes"))
        filings = await sec.to_read(day) if sec else {}
        await reader.read(repo.get(run_id).evidence, ctx.config.research.read_articles, as_of,
                          lambda n, total: repo.set_stage(run_id, f"reading {n} of {total}"), filings)
        repo.set_stage(run_id, None)
    findings, cited = keep_citations(outcome.findings, shown)
    statistics, data_id = await _statistics(ctx, shelf, run_id, listing, as_of, day)
    run = repo.get(run_id)
    evidence = tuple(e for e in run.evidence if e.id != data_id)
    lines = {e.id: evidence_entry(e) for e in evidence}
    read = frozenset(e.id for e in evidence if e.facts)
    digest_text = digest(statistics, [lines[e.id] for e in evidence if e.id in read],
                         [lines[e.id] for e in evidence if e.id not in read], findings,
                         digest_room(stages.context("roles")))
    report = await write_report(stages.model("roles"), digest_text, shown, day)
    if not report.usable:
        with transaction(ctx.conn):
            repo.set_cited(run_id, cited)
            _finish(ctx, repo, run_id, stages, RunStatus.FAILED, findings, UNUSABLE)
        return
    material = Material(statistics, data_id, findings, report, evidence, run.steps, lines, read)
    report = await add_verdict(stages.model("verdict"), report, material, stages.context("verdict"))
    # One transaction, so a run never ends up failed beside a half-saved report.
    with transaction(ctx.conn):
        repo.save_report(run_id, listing, report)
        repo.set_cited(run_id, cited | report.cites)
        _finish(ctx, repo, run_id, stages, outcome.status, findings, None)


def _finish(ctx: AppContext, repo: ResearchRepo, run_id: int, stages: _Stages, status: RunStatus,
            findings: str | None, error: str | None) -> None:
    records = stages.records()
    repo.finish(run_id, status, findings, error, _now(ctx), {m for s in records for m in s.fell_back_to}, records)


@dataclass(frozen=True)
class Refresh:
    """One pass over the watched listings' stale research."""

    due: int
    researched: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    """Listing and error."""
    deferred: list[str] = field(default_factory=list)
    """Left for the next pass, for `deferred_why`."""
    deferred_why: str | None = None


async def refresh_research(ctx: AppContext, progress: Callable[[str], None] = lambda _: None,
                           run: Callable[[AppContext, str], Awaitable[object]] | None = None,
                           budget_min: float | None = None) -> Refresh:
    """Research the watched listings whose report is missing, too old or behind a price move
    (`[ai_account]`), most stale first, starting runs only while the time budget (`budget_min`,
    else the tick's) lasts. `progress` hears each listing as it starts; `run` defaults to `research`
    on the ai_account slot's profile when set, else on the slots like any run."""
    run = run or (lambda c, symbol: research(c, symbol, profile=c.config.model_slots.get("ai_account")))
    budget = timedelta(minutes=ctx.config.ai_account.research_budget_min if budget_min is None else budget_min)
    started = ctx.clock.utcnow()
    due = stale_research(ctx)
    done = Refresh(len(due))
    if due and pc_in_use(ctx):
        return _defer(done, due, "PC in use")
    for i, symbol in enumerate(due):
        if ctx.clock.utcnow() - started >= budget:
            return _defer(done, due[i:], "time budget spent")
        progress(f"research {i + 1} of {len(due)}: {symbol}")
        try:
            await run(ctx, symbol)
        except JobAlreadyRunning:
            # A manual run holds the lock for minutes: leave the rest to the next tick.
            return _defer(done, due[i:], "research is busy")
        except Exception as exc:
            if not isinstance(exc, ServiceError):
                log.exception("research on %s failed", symbol)
            done.failed.append((symbol, str(exc) or type(exc).__name__))
        else:
            done.researched.append(symbol)
    return done


def stale_research(ctx: AppContext) -> list[str]:
    """The watched listings whose report is missing, has no valid verdict, is too old or is
    behind a price move (`[ai_account]`), in the order a tick researches them: the user's own
    picks before discovered ones."""
    ai = ctx.config.ai_account
    known = [_known(ctx, s) for s in WatchlistRepo(ctx.conn).all_symbols()]
    due = due_for_research(known, ctx.clock.utcnow().date(), ai.max_verdict_age_days, ai.move_threshold)
    discovered = set(discovered_only(ctx))
    return sorted((d.symbol for d in due), key=lambda s: s in discovered)


def pc_in_use(ctx: AppContext) -> bool:
    """With `research_when_idle`, whether someone used the PC too recently for research to
    take its GPU. A PC that cannot tell counts as idle, so research is never held off forever."""
    s = ctx.config.schedule
    if not s.research_when_idle:
        return False
    seconds = idle.idle_seconds()
    return seconds is not None and seconds < s.idle_minutes * 60


def _defer(done: Refresh, left: list[str], why: str) -> Refresh:
    return Refresh(done.due, done.researched, done.failed, left, why)


def _known(ctx: AppContext, symbol: str) -> Known:
    repo = ResearchRepo(ctx.conn)
    latest = repo.latest_report(symbol)
    day = latest.report.day if latest else None
    bars = BarRepo(ctx.conn)
    return Known(symbol, day, bars.close_on(symbol, day) if day else None, bars.close_on(symbol),
                 bool(latest and latest.report.verdict), repo.last_attempt(symbol))


def _record_failure(ctx: AppContext, repo: ResearchRepo, run_id: int, stages: _Stages, exc: BaseException) -> None:
    """Never leave a run marked running (the GUI would show it as the latest forever), and
    never let a failure to record that hide the error itself."""
    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
        error = "cancelled"
    else:
        error = str(exc) if isinstance(exc, ModelError) else f"unexpected error: {exc!r}"
    try:
        _finish(ctx, repo, run_id, stages, RunStatus.FAILED, None, error)
    except Exception as also:
        exc.add_note(f"the run could not be marked failed either: {also!r}")


async def _statistics(ctx: AppContext, shelf: Shelf, run_id: int, listing: str, as_of: date | None,
                      day: date) -> tuple[str, int]:
    """The stored data as one citable item (kept with the run, so the report can be checked
    later), as the roles read it, and its id."""
    signals = "; ".join(f"{s.signal.analyst}: {s.signal.direction.value} ({s.signal.reason})"
                        + (" [out of date]" if s.stale else "")
                        for s in latest_signals(ctx, listing) if s.signal.as_of <= day)
    data = await market_summary(ctx, listing, as_of) + (f" Analyst signals: {signals}." if signals else "")
    [data_id] = shelf.keep([Article(f"oracle:data/{listing}/run-{run_id}",
                                    f"{listing} stored prices, fundamentals and analyst signals", "stored data",
                                    datetime.combine(day, time(), timezone.utc), body=data)], "oracle")
    return f"[a{data_id}] {day.isoformat()} stored data: {data}", data_id


def check_as_of(ctx: AppContext, as_of: date | None) -> None:
    today = ctx.clock.utcnow().date()
    if as_of and as_of > today:
        raise InvalidQuery(f"as-of date {as_of.isoformat()} is in the future (today is {today.isoformat()})")


def research_run(ctx: AppContext, symbol: str, run_id: int) -> ResearchRun:
    """One run of the listing, whatever its outcome."""
    listing = resolve_symbol(ctx, symbol).symbol
    run = ResearchRepo(ctx.conn).get(run_id)
    if run is None or run.symbol != listing:
        raise UnknownResearchRun(f"no research run {run_id} for {listing}")
    return run


def report_history(ctx: AppContext, symbol: str) -> list[ResearchRun]:
    """Every run with a report for the listing, newest run first."""
    return ResearchRepo(ctx.conn).with_reports(resolve_symbol(ctx, symbol).symbol)


def _tools(ctx: AppContext, client: RateLimitedClient, shelf: Shelf, listing: str, country: str | None,
           as_of: date | None, searcher: WebSearcher | None, gate: SourceGate, sec: Filings | None) -> list[Tool]:
    tools = [MarketData(ctx, listing, as_of), NewsSearch(client, shelf), GdeltSearch(client, shelf),
             RelatedCompanies(ctx, listing, as_of)]
    if country in EDITION:
        tools.insert(2, LocalNewsSearch(client, shelf, country))
    if sec:
        tools.append(sec)
    if searcher is not None:
        tools += [WebSearch(searcher, gate, shelf), ReadPage(client, shelf, ctx.config.research.contact)]
    return tools


def research_progress(ctx: AppContext, listing: str) -> str | None:
    """Where the listing's running research is, for a progress line; None when none runs."""
    run = ResearchRepo(ctx.conn).latest(listing)
    if run is None or run.status != RunStatus.RUNNING:
        return None
    if stage := ResearchRepo(ctx.conn).stage(run.id):
        return stage
    if not run.steps:
        return "planning searches"
    last = run.steps[-1]
    return "writing the report" if last.action == "final" else f"step {last.n}: {last.action}"


def sweep_interrupted(ctx: AppContext) -> None:
    """Mark runs a stopped app left `running` as failed, unless another process is still
    doing research (its lock is live), so no page shows a dead run as running forever."""
    if any(s.job == JOB and s.stale_reason is None for s in job_statuses(ctx)):
        return
    ResearchRepo(ctx.conn).fail_running(INTERRUPTED, _now(ctx))


def latest_research(ctx: AppContext, symbol: str) -> ResearchRun | None:
    return ResearchRepo(ctx.conn).latest(resolve_symbol(ctx, symbol).symbol)


def latest_verdict(ctx: AppContext, symbol: str) -> ResearchRun | None:
    """The newest live report's run when that report has a valid verdict: a newer report whose
    verdict was rejected supersedes every older one. Replays never count."""
    run = ResearchRepo(ctx.conn).latest_report(resolve_symbol(ctx, symbol).symbol)
    return run if run and run.report.verdict else None


def _about(p: ListingProfile, day: date) -> str:
    # The date matters twice: "recent" needs an anchor, and a replay must not lean on what
    # the model remembers from after it.
    where = ", ".join(x for x in (p.exchange, p.country, p.sector, p.industry) if x)
    return (f"Today is {day.isoformat()}; use nothing you know from after it. "
            f"Company: {p.name} ({p.symbol}){': ' + where if where else ''}.")


def _task(p: ListingProfile, day: date, queries: list[Search]) -> str:
    searches = "\n".join(f"- {q.tool}: {q.query}" for q in queries)
    return (f"{_about(p, day)}\nResearch it. Start with these searches (tool: query), then follow what you "
            f"find:\n{searches}")


def _now(ctx: AppContext) -> str:
    return to_utc_iso(ctx.clock.utcnow())
