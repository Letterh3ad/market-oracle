"""The midday pass: discovery, then research on stale listings, and nothing else."""

from dataclasses import replace
from datetime import date

import pytest

from oracle.broker.orders import Side
from oracle.services import research_service, tick_service
from oracle.services.activity_service import JobRunner, retry, submit_midday
from oracle.services.discovery_service import DiscoveryRun
from oracle.services.tick_service import MIDDAY_STAGES, last_midday, last_tick, run_midday, ticked_since
from oracle.services.watch_service import unwatch
from oracle.store.discoveries import DISCOVERED_LIST
from oracle.store.paper import PaperRepo
from oracle.store.watchlists import WatchlistRepo
from tests.services.test_tick_discovery import DENSO, discovering, settings  # noqa: F401
from tests.services.test_tick_research import ctx, fake_research  # noqa: F401


@pytest.fixture
def researching(ctx, monkeypatch):
    run = fake_research(ctx)
    monkeypatch.setattr(research_service, "research", run)
    return run


def stage(run, name):
    return next(s for s in run.stages if s.name == name)


def orders(ctx):
    return ctx.conn.execute("SELECT id, status FROM paper_orders ORDER BY id").fetchall()


async def test_it_discovers_then_researches_and_is_stored_apart_from_ticks(ctx, discovering, researching):
    tick = last_tick(ctx)
    ctx.clock.advance(60)
    discovering.result[0] = DiscoveryRun((DENSO,), seeds=("7203.T",))
    run = await run_midday(ctx)
    assert [s.name for s in run.stages] == list(MIDDAY_STAGES) == ["discover", "questions", "research"]
    assert stage(run, "questions").summary == "nothing asked: no question is on auto"
    assert (run.kind, run.trigger, run.outcome) == ("midday", "manual", "ok")
    assert run.stages[0].summary.startswith("added 6902.T")
    assert researching.done == ["AAPL", "IBM", "MSFT"]
    assert last_midday(ctx).id == run.id and last_tick(ctx).id == tick.id
    assert not ticked_since(ctx, run.started_at) and ticked_since(ctx, run.started_at, "midday")


async def test_it_never_places_or_fills_orders(ctx, discovering, researching, monkeypatch):
    PaperRepo(ctx.conn).add_order("ai", "AAPL", Side.BUY, 1000.0, None, "2026-09-21T19:00:00Z",
                                  date(2026, 9, 21), "test")
    before = orders(ctx)

    def forbidden(*_, **__):
        raise AssertionError("the midday pass ran a tick-only step")

    for name in ("update_watched", "update_fx", "fill_pending", "analyse_watched", "strategy_step", "ai_step",
                 "regime_step"):
        monkeypatch.setattr(tick_service, name, forbidden)
    run = await run_midday(ctx)
    assert run.outcome == "ok" and researching.done
    assert orders(ctx) == before
    assert ctx.conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0


async def test_own_picks_are_researched_before_discovered_ones(ctx, discovering, researching):
    unwatch(ctx, "AAPL")
    WatchlistRepo(ctx.conn).add(DISCOVERED_LIST, "AAPL", "2026-09-21T20:00:00Z")
    await run_midday(ctx)
    assert researching.done == ["IBM", "MSFT", "AAPL"]


async def test_its_budget_covers_discovery_and_research(ctx, discovering, monkeypatch):
    settings(ctx, "schedule", midday_budget_min=30)
    run = fake_research(ctx, minutes=20)
    monkeypatch.setattr(research_service, "research", run)

    async def slow_discovery(c):
        c.clock.advance(5 * 60)
        return DiscoveryRun(seeds=("AAPL",))

    monkeypatch.setattr(tick_service, "discover", slow_discovery)
    research = stage(await run_midday(ctx), "research")
    # Discovery took 5 of 30 minutes; runs start at 5 and 25, and at 45 the budget is spent.
    assert run.done == ["AAPL", "IBM"] and "1 deferred (time budget spent)" in research.summary


def test_discovery_gets_at_most_a_third_of_the_budget(ctx):
    assert tick_service.discover_cap_min(ctx, 60) == 10
    assert tick_service.discover_cap_min(ctx, 12) == 4


@pytest.mark.parametrize("section, values", [("automation", {"enabled": False}), ("ai_account", {"enabled": False})])
async def test_switches_off_hold_back_its_stages_as_they_do_a_ticks(ctx, discovering, researching, section, values):
    settings(ctx, section, **values)
    run = await run_midday(ctx)
    assert run.outcome == "ok" and ("nothing" in stage(run, "discover").summary
                                    or "nothing" in stage(run, "research").summary)


async def test_the_pc_in_use_holds_it_back_with_research_when_idle(ctx, discovering, researching, monkeypatch):
    monkeypatch.setattr(research_service.idle, "idle_seconds", lambda: 5.0)
    settings(ctx, "schedule", research_when_idle=True)
    run = await run_midday(ctx)
    assert discovering.calls == [] and researching.done == []
    assert stage(run, "discover").summary == "PC in use: discovery waits for the next midday pass"
    assert stage(run, "questions").summary == "PC in use: discovery waits for the next midday pass"
    assert "PC in use" in stage(run, "research").summary


async def test_as_a_job_it_is_labelled_and_a_retry_runs_another_midday_pass(ctx, discovering, researching):
    runner = JobRunner(ctx)
    job = submit_midday(ctx, runner)
    assert (job.kind, job.label, job.subject) == ("tick", "Midday pass (manual)", "midday")
    await runner.wait(job.id)
    assert job.result.kind == "midday"
    job.state = "failed"
    again = retry(ctx, runner, job.id)
    assert again.label == "Midday pass (manual)"
    await runner.wait(again.id)
