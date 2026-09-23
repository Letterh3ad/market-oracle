from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.config import Config
from oracle.research.trace import Report, RunStatus
from oracle.services import research_service
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.jobs_service import single_flight
from oracle.services.paper_service import decision_log
from oracle.services.research_service import latest_research, refresh_research
from oracle.services.tick_service import run_tick
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from oracle.store.research import ResearchRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.llm import ScriptedModel, step
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)
SYMBOLS = ("AAPL", "MSFT", "IBM")


@pytest.fixture
async def ctx(tmp_path):
    yahoo = FakeYahoo()
    for s in SYMBOLS:
        yahoo.add(s, "USD", "America/New_York", [bar("2026-09-10", 100.0), bar("2026-09-18", 100.0)])
    yahoo.add("GBPUSD=X", "USD", "Europe/London", [bar("2026-09-17", 1.25), bar("2026-09-18", 1.25)])
    web = FakeWeb(FakeFinanceDatabase([equity(s, s.title(), currency="USD") for s in SYMBOLS]), yahoo)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        for s in SYMBOLS:
            watch(c, s)
        await run_tick(c)  # stores the bars
        c.config = replace(c.config, ai_account=replace(c.config.ai_account, enabled=True))
        yield c


def reported(ctx, symbol, day):
    repo = ResearchRepo(ctx.conn)
    run = repo.start(symbol, "ollama", "m", f"{day.isoformat()}T12:00:00Z")
    repo.save_report(run, symbol, Report(day, (), None, None, (), None))
    repo.finish(run, RunStatus.DONE, "notes", None, f"{day.isoformat()}T12:05:00Z")


def fake_research(ctx, minutes=0, fail=()):
    """Writes a report dated today, taking `minutes` of the fake clock."""
    done = []

    async def run(c, symbol):
        if symbol in fail:
            raise InvalidQuery(f"{symbol} broke")
        ctx.clock.advance(minutes * 60)
        reported(c, symbol, c.clock.utcnow().date())
        done.append(symbol)

    run.done = done
    return run


async def test_research_runs_on_stale_listings_most_stale_first_saying_where_it_is(ctx):
    reported(ctx, "AAPL", date(2026, 9, 20))  # fresh: left alone
    reported(ctx, "MSFT", date(2026, 9, 1))
    said = []
    run = fake_research(ctx)
    done = await refresh_research(ctx, said.append, run)
    assert run.done == ["IBM", "MSFT"]
    assert said == ["research 1 of 2: IBM", "research 2 of 2: MSFT"]
    assert (done.due, done.researched, done.deferred, done.failed) == (2, ["IBM", "MSFT"], [], [])


async def test_a_price_move_since_the_report_makes_it_due(ctx):
    for s in SYMBOLS:
        reported(ctx, s, date(2026, 9, 11))  # the close then was 100
    ctx.config = replace(ctx.config, ai_account=replace(ctx.config.ai_account, move_threshold=0.5,
                                                         max_verdict_age_days=30))
    assert (await refresh_research(ctx, run=fake_research(ctx))).due == 0
    ctx.conn.execute("UPDATE bars SET close = 160 WHERE symbol = 'MSFT' AND date = '2026-09-18'")
    assert (await refresh_research(ctx, run=fake_research(ctx))).researched == ["MSFT"]


async def test_the_budget_stops_new_runs_and_the_rest_wait_for_the_next_tick(ctx):
    run = fake_research(ctx, minutes=20)
    first = await refresh_research(ctx, run=run)
    # Runs start at 0 and 20 minutes; at 40 the 30-minute budget is spent.
    assert (first.researched, first.deferred, first.deferred_why) == (["AAPL", "IBM"], ["MSFT"], "time budget spent")
    second = await refresh_research(ctx, run=run)
    assert (second.due, second.researched, second.deferred) == (1, ["MSFT"], [])


async def test_a_failed_listing_is_counted_and_the_others_still_run(ctx):
    done = await refresh_research(ctx, run=fake_research(ctx, fail={"MSFT"}))
    assert done.researched == ["AAPL", "IBM"] and done.failed == [("MSFT", "MSFT broke")]


async def test_a_manual_research_holding_the_lock_defers_instead_of_failing(ctx):
    with single_flight(ctx, "research", 60.0):
        done = await refresh_research(ctx)
    assert (done.researched, done.failed, done.deferred) == ([], [], sorted(SYMBOLS))
    assert done.deferred_why == "research is busy"


def _stage(run, name):
    return next(s for s in run.stages if s.name == name)


async def test_ai_off_skips_research_and_the_ai_step_saying_so(ctx):
    ctx.config = replace(ctx.config, ai_account=replace(ctx.config.ai_account, enabled=False))
    run = await run_tick(ctx)
    assert [s.name for s in run.stages] == ["prices", "fx", "fill", "analyse", "research", "step", "ai_step"]
    assert _stage(run, "research").summary == "AI account off: nothing researched"
    assert _stage(run, "ai_step").summary == "AI account off: nothing traded"
    assert latest_research(ctx, "AAPL") is None and decision_log(ctx, "ai") == []


async def test_a_tick_researches_with_the_configured_model_then_runs_the_ai_step(ctx, monkeypatch):
    model = ScriptedModel(*[step("final", "Nothing new.")] * len(SYMBOLS))
    monkeypatch.setattr(research_service, "configured_model", lambda c: model)
    reported(ctx, "MSFT", date(2026, 9, 20))
    said = []
    run = await run_tick(ctx, on_stage=said.append)
    assert _stage(run, "research").summary == "2 of 2 due researched"
    assert "research 1 of 2: AAPL" in said and said.index("research") < said.index("research 1 of 2: AAPL")
    assert latest_research(ctx, "AAPL").report is not None
    assert {d.symbol for d in decision_log(ctx, "ai")} == set(SYMBOLS)
    assert _stage(run, "ai_step").summary.endswith("3 skip, 0 no change")


async def test_the_research_stage_shows_deferred_and_failed_counts(ctx, monkeypatch):
    async def broken(c, symbol, *a, **k):
        raise InvalidQuery("model gone")

    monkeypatch.setattr(research_service, "research", broken)
    run = await run_tick(ctx)
    stage = _stage(run, "research")
    assert (stage.state, stage.summary) == ("partial", "0 of 3 due researched, 3 failed")
    assert stage.error == "AAPL: model gone"
    monkeypatch.undo()
    with single_flight(ctx, "research", 60.0):
        stage = _stage(await run_tick(ctx), "research")
    assert (stage.state, stage.summary) == ("ok", "0 of 3 due researched, 3 deferred (research is busy)")
