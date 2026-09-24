"""The tick's discover stage, and discovered listings in the research order."""

import asyncio
from dataclasses import replace

import pytest

from oracle.services import discovery_service, tick_service
from oracle.services.discovery_service import Added, DiscoveryRun, Skipped
from oracle.services.errors import JobAlreadyRunning
from oracle.services.research_service import refresh_research, stale_research
from oracle.services.tick_service import STAGES, discovery_line, run_tick
from oracle.services.watch_service import unwatch
from oracle.store.discoveries import DISCOVERED_LIST, DiscoveryRepo
from oracle.store.watchlists import WatchlistRepo
from tests.fakes.llm import ScriptedModel
from tests.services.test_tick_research import ctx, fake_research  # noqa: F401

DENSO = Added("6902.T", "DENSO Corporation", "7203.T", "peer", "same industry as 7203.T", ())


def stage(run):
    return next(s for s in run.stages if s.name == "discover")


@pytest.fixture
def discovering(monkeypatch):
    """The stage's discovery run, faked: returns `result[0]` (or raises it), counting calls."""
    result = [DiscoveryRun()]
    calls = []

    async def fake(c):
        calls.append(c)
        if isinstance(result[0], BaseException):
            raise result[0]
        return result[0]

    monkeypatch.setattr(tick_service, "discover", fake)
    fake.result, fake.calls = result, calls
    return fake


def settings(ctx, section, **values):
    ctx.config = replace(ctx.config, **{section: replace(getattr(ctx.config, section), **values)})


async def test_discover_runs_just_before_research(ctx, discovering):
    names = list(STAGES)
    assert names.index("discover") == names.index("research") - 1
    run = await run_tick(ctx)
    assert [s.name for s in run.stages] == names and len(discovering.calls) == 1


async def test_what_was_added_reads_in_plain_words(ctx, discovering):
    discovering.result[0] = DiscoveryRun((DENSO,), seeds=("7203.T",))
    s = stage(await run_tick(ctx))
    assert (s.state, s.summary) == ("ok", "added 6902.T DENSO Corporation (same industry as 7203.T)")


@pytest.mark.parametrize("section, values, why", [
    ("discovery", {"enabled": False}, "discovery is off in Settings: nothing discovered"),
    ("automation", {"enabled": False}, "automation is off: nothing discovered"),
])
async def test_off_it_never_runs_and_says_why(ctx, discovering, section, values, why):
    settings(ctx, section, **values)
    s = stage(await run_tick(ctx))
    assert (s.state, s.summary) == ("ok", why) and discovering.calls == []


async def test_nothing_added_says_why(ctx, discovering):
    discovering.result[0] = DiscoveryRun(note="the discovered list is full (10 of 10)")
    assert stage(await run_tick(ctx)).summary == "nothing added: the discovered list is full (10 of 10)"
    discovering.result[0] = DiscoveryRun(skipped=(Skipped("Ford", "already watched"), Skipped("GM", "already watched"),
                                                  Skipped("Tesla", "on the never list")), seeds=("AAPL", "IBM"))
    assert stage(await run_tick(ctx)).summary == ("nothing added from AAPL, IBM: 3 passed over "
                                                  "(2 already watched, 1 on the never list)")
    discovering.result[0] = DiscoveryRun(seeds=("AAPL",))
    assert stage(await run_tick(ctx)).summary == "nothing added from AAPL: no companies found"


async def test_a_failed_source_is_partial_and_the_tick_goes_on(ctx, discovering):
    discovering.result[0] = DiscoveryRun((DENSO,), seeds=("7203.T",), source_errors=("news for 7203.T: timed out",))
    run = await run_tick(ctx)
    assert (stage(run).state, stage(run).error) == ("partial", "news for 7203.T: timed out")
    assert all(s.state != "failed" for s in run.stages)


async def test_a_crash_never_fails_the_tick(ctx, discovering):
    discovering.result[0] = RuntimeError("boom")
    run = await run_tick(ctx)
    assert stage(run).state == "failed" and run.outcome == "partial"
    assert next(s for s in run.stages if s.name == "research").state != "failed"


async def test_a_discovery_already_running_is_left_to_finish(ctx, discovering):
    discovering.result[0] = JobAlreadyRunning("discovery is already running")
    s = stage(await run_tick(ctx))
    assert (s.state, s.summary) == ("ok", "a discovery run is already going (Discover now): nothing more this tick")


def test_the_time_cap_is_ten_minutes_or_a_third_of_the_research_budget(ctx):
    assert tick_service.discover_cap_min(ctx) == 10
    settings(ctx, "ai_account", research_budget_min=12)
    assert tick_service.discover_cap_min(ctx) == 4


async def test_the_time_cap_stops_it_and_keeps_what_it_added(ctx, monkeypatch):
    async def slow(c):
        DiscoveryRepo(c.conn).add("IBM", "ibm", "Ibm", "related", "AAPL", "competitor", (), "2026-09-21T20:00:00Z")
        await asyncio.sleep(5)

    monkeypatch.setattr(tick_service, "discover", slow)
    monkeypatch.setattr(tick_service, "discover_cap_min", lambda c: 0.001)
    s = stage(await run_tick(ctx))
    assert s.state == "ok"
    assert s.summary == "stopped at its time limit; added IBM Ibm (named as a competitor of AAPL in the news)"


async def test_a_real_run_through_the_stage(ctx, monkeypatch):
    monkeypatch.setattr(discovery_service, "model_for", lambda c, p: ScriptedModel())
    s = stage(await run_tick(ctx))
    assert s.state in ("ok", "partial") and s.summary.startswith("nothing added")


async def test_watched_listings_are_researched_before_discovered_ones(ctx):
    unwatch(ctx, "AAPL")
    WatchlistRepo(ctx.conn).add(DISCOVERED_LIST, "AAPL", "2026-09-21T20:00:00Z")
    assert stale_research(ctx) == ["IBM", "MSFT", "AAPL"]
    run = fake_research(ctx)
    await refresh_research(ctx, run=run)
    assert run.done == ["IBM", "MSFT", "AAPL"]


async def test_the_ledgers_line_says_what_discovery_did(ctx, discovering):
    assert discovery_line(ctx).startswith("Discovery: 0 discovered companies")
    discovering.result[0] = DiscoveryRun((DENSO,), seeds=("7203.T",))
    await run_tick(ctx)
    line = discovery_line(ctx)
    assert "traded by the AI account" in line and "added 6902.T" in line
    settings(ctx, "discovery", ai_trades=False)
    assert "not traded" in discovery_line(ctx)


async def test_a_pc_in_use_holds_discovery_like_research(ctx, discovering, monkeypatch):
    from oracle.system import idle

    settings(ctx, "schedule", research_when_idle=True, idle_minutes=10)
    monkeypatch.setattr(idle, "idle_seconds", lambda: 60.0)
    s = stage(await run_tick(ctx))
    assert s.summary == "PC in use: discovery waits for the next tick" and discovering.calls == []
