import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.config import Config
from oracle.research.trace import Report, RunStatus, Verdict
from oracle.services import research_service
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.jobs_service import single_flight
from oracle.services.paper_service import decision_log
from oracle.services.activity_service import JobRunner, queue_stale_research, stale_research_jobs, submit_research
from oracle.services.research_service import latest_research, refresh_research, stale_research
from oracle.services.tick_service import run_tick
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from oracle.store.research import ResearchRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.llm import ScriptedModel, step, use_model
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


def reported(ctx, symbol, day, rejected=None):
    """A live run on `day` with a report carrying a verdict, or `rejected` saying why it has none."""
    repo = ResearchRepo(ctx.conn)
    run = repo.start(symbol, "ollama", "m", f"{day.isoformat()}T12:00:00Z")
    call = None if rejected else Verdict("hold", 0.5, "Flat [a1]", (1,), "it moves")
    repo.save_report(run, symbol, Report(day, (), None, None, (), None, verdict=call, verdict_rejected=rejected))
    repo.finish(run, RunStatus.DONE, "notes", None, f"{day.isoformat()}T12:05:00Z")


def fake_research(ctx, minutes=0, fail=()):
    """Writes a report dated today, taking `minutes` of the fake clock; a listing in `fail`
    records a failed run and raises, as real research does."""
    done = []

    async def run(c, symbol, **_):
        if symbol in fail:
            repo = ResearchRepo(c.conn)
            now = c.clock.utcnow().isoformat()
            repo.finish(repo.start(symbol, "ollama", "m", now), RunStatus.FAILED, None, "broke", now)
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
    run = fake_research(ctx, minutes=80)
    first = await refresh_research(ctx, run=run)
    # Runs start at 0 and 80 minutes; at 160 the 120-minute budget is spent.
    assert (first.researched, first.deferred, first.deferred_why) == (["AAPL", "IBM"], ["MSFT"], "time budget spent")
    second = await refresh_research(ctx, run=run)
    assert (second.due, second.researched, second.deferred) == (1, ["MSFT"], [])


async def test_a_failed_listing_is_counted_and_the_others_still_run(ctx):
    done = await refresh_research(ctx, run=fake_research(ctx, fail={"MSFT"}))
    assert done.researched == ["AAPL", "IBM"] and done.failed == [("MSFT", "MSFT broke")]


async def test_a_newest_report_without_a_valid_verdict_is_due(ctx):
    for s in SYMBOLS:
        reported(ctx, s, date(2026, 9, 19))
    reported(ctx, "MSFT", date(2026, 9, 20), rejected="its reason cites no evidence")
    done = await refresh_research(ctx, run=fake_research(ctx))
    assert (done.due, done.researched) == (1, ["MSFT"])


async def test_a_listing_whose_research_keeps_failing_goes_last_on_the_next_tick(ctx):
    run = fake_research(ctx, fail={"AAPL"})
    await refresh_research(ctx, run=run)
    ctx.clock.advance(8 * 86400)  # every report now too old
    said = []
    await refresh_research(ctx, said.append, run)
    assert said == ["research 1 of 3: IBM", "research 2 of 3: MSFT", "research 3 of 3: AAPL"]
    assert (await refresh_research(ctx, run=fake_research(ctx, fail={"AAPL"}))).due == 1


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
    assert [s.name for s in run.stages] == ["prices", "fx", "fill", "analyse", "regime_step", "regime_ai_step", "regime_v2_step", "core_sat_step", "laya_step", "discover", "research", "step",
        "ai_step", "industry"]
    assert _stage(run, "research").summary == "AI account off: nothing researched"
    assert _stage(run, "ai_step").summary == "AI account off: nothing traded"
    assert latest_research(ctx, "AAPL") is None and decision_log(ctx, "ai") == []


async def test_a_tick_researches_with_the_configured_model_then_runs_the_ai_step(ctx, monkeypatch):
    model = ScriptedModel(*[step("final", "Nothing new.")] * len(SYMBOLS))
    use_model(monkeypatch, research_service, model)
    reported(ctx, "MSFT", date(2026, 9, 20))
    said = []
    run = await run_tick(ctx, on_stage=said.append)
    assert _stage(run, "research").summary == "2 of 2 due researched (unvalidated)"
    assert "research 1 of 2: AAPL" in said and said.index("research") < said.index("research 1 of 2: AAPL")
    assert latest_research(ctx, "AAPL").report is not None
    # MSFT's hold verdict leaves it as it is; the scripted runs' verdicts are rejected.
    assert {d.symbol for d in decision_log(ctx, "ai")} == {"AAPL", "IBM"}
    assert _stage(run, "ai_step").summary.endswith("2 skip, 1 no change (unvalidated)")


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


@pytest.fixture
def idle_for(monkeypatch, ctx):
    """Research waits for an idle PC (10 minutes); the test sets how long the PC has been idle."""
    from oracle.system import idle

    ctx.config = replace(ctx.config, schedule=replace(ctx.config.schedule, research_when_idle=True, idle_minutes=10))
    seconds = [0.0]
    monkeypatch.setattr(idle, "idle_seconds", lambda: seconds[0])
    return seconds


async def test_a_pc_in_use_defers_research_while_the_light_stages_run(ctx, idle_for):
    idle_for[0] = 9 * 60
    run = await run_tick(ctx)
    assert _stage(run, "research").summary == "0 of 3 due researched, 3 deferred (PC in use)"
    assert all(s.state == "ok" for s in run.stages)


async def test_an_idle_pc_or_one_that_cannot_tell_researches(ctx, idle_for, monkeypatch):
    from oracle.system import idle

    idle_for[0] = 10 * 60
    assert (await refresh_research(ctx, run=fake_research(ctx))).researched == ["AAPL", "IBM", "MSFT"]
    monkeypatch.setattr(idle, "idle_seconds", lambda: None)
    ctx.clock.advance(8 * 86400)
    assert (await refresh_research(ctx, run=fake_research(ctx))).deferred == []


async def test_the_idle_gate_is_off_by_default(ctx, monkeypatch):
    from oracle.system import idle

    monkeypatch.setattr(idle, "idle_seconds", lambda: 0.0)
    assert (await refresh_research(ctx, run=fake_research(ctx))).deferred == []


async def test_stale_research_lists_what_a_tick_would_research_in_the_same_order(ctx):
    reported(ctx, "AAPL", date(2026, 9, 20))
    reported(ctx, "MSFT", date(2026, 9, 1))
    assert stale_research(ctx) == ["IBM", "MSFT"]
    run = fake_research(ctx)
    await refresh_research(ctx, run=run)
    assert run.done == ["IBM", "MSFT"] and stale_research(ctx) == []


@pytest.fixture
async def runner(ctx, monkeypatch):
    from oracle.services import activity_service

    async def stalled(c, symbol, **kw):
        await asyncio.Event().wait()

    monkeypatch.setattr(activity_service, "research", stalled)
    r = JobRunner(ctx)
    yield r
    await r.stop()


async def test_research_all_stale_queues_each_due_listing_in_tick_order(ctx, runner):
    reported(ctx, "AAPL", date(2026, 9, 20))
    queued = queue_stale_research(ctx, runner)
    assert [j.subject for j in queued.jobs] == ["IBM", "MSFT"] and queued.waiting == [] and queued.refused == []
    assert [j.label for j in runner.current()] == ["Research IBM", "Research MSFT"]


async def test_research_all_stale_twice_never_queues_a_listing_again(ctx, runner):
    first = queue_stale_research(ctx, runner)
    again = queue_stale_research(ctx, runner)
    assert again.jobs == [] and again.waiting == ["AAPL", "IBM", "MSFT"]
    assert len(runner.current()) == len(first.jobs) == 3


async def test_a_listing_already_queued_by_its_own_button_is_left_out(ctx, runner):
    submit_research(ctx, runner, "MSFT")
    stale = stale_research_jobs(ctx, runner)
    assert (stale.due, stale.waiting, stale.to_queue) == (["AAPL", "IBM", "MSFT"], ["MSFT"], ["AAPL", "IBM"])
    assert [j.subject for j in queue_stale_research(ctx, runner).jobs] == ["AAPL", "IBM"]
    assert [j.subject for j in runner.current()] == ["MSFT", "AAPL", "IBM"]


async def test_a_replay_queued_for_a_listing_does_not_count_as_its_research(ctx, runner):
    submit_research(ctx, runner, "MSFT", as_of=date(2026, 9, 1))
    assert stale_research_jobs(ctx, runner).waiting == []


async def test_nothing_stale_queues_nothing(ctx, runner):
    for s in SYMBOLS:
        reported(ctx, s, date(2026, 9, 20))
    queued = queue_stale_research(ctx, runner)
    assert (queued.jobs, queued.waiting, runner.current()) == ([], [], [])


async def test_a_watched_listing_gone_from_the_universe_is_refused_and_the_rest_still_queue(ctx, runner):
    with ctx.conn:
        ctx.conn.execute("DELETE FROM listings WHERE symbol = 'IBM'")
    queued = queue_stale_research(ctx, runner)
    assert [j.subject for j in queued.jobs] == ["AAPL", "MSFT"]
    [(symbol, why)] = queued.refused
    assert symbol == "IBM" and "no listing 'IBM'" in why
