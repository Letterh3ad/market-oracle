from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.prices_service import update_listing
from oracle.services.research_service import latest_research, research
from oracle.services.universe_service import refresh_universe
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.llm import ScriptedModel, step
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
async def ctx(tmp_path):
    yahoo = FakeYahoo()
    yahoo.add("AAPL", "USD", "America/New_York", [bar("2026-09-18", 100.0), bar("2026-09-21", 105.0)])
    web = FakeWeb(FakeFinanceDatabase([equity("AAPL", "Apple Inc.", currency="USD")]), yahoo)
    manager = open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport)
    c = manager.__enter__()
    await refresh_universe(c)
    await update_listing(c, "AAPL")
    yield c
    manager.__exit__(None, None, None)


async def test_a_run_looks_up_market_data_then_answers_and_is_saved(ctx):
    model = ScriptedModel(step("market_data", "AAPL"), step("final", "Up 5% on the day.", thought="enough"))
    run = await research(ctx, "aapl", model)
    assert run.symbol == "AAPL" and run.status == "done"
    assert run.findings == "Up 5% on the day."
    lookup, final = run.steps
    assert lookup.action == "market_data" and "105" in lookup.observation
    assert final.action == "final" and final.thought == "enough"
    assert latest_research(ctx, "AAPL") == run


def with_steps(ctx, n):
    from dataclasses import replace

    ctx.config = replace(ctx.config, llm=replace(ctx.config.llm, max_steps=n))


async def test_at_the_step_cap_the_run_is_made_to_answer_and_says_it_was_capped(ctx):
    with_steps(ctx, 2)
    model = ScriptedModel(step("market_data"), step("market_data"), step("final", "Partial view."))
    run = await research(ctx, "AAPL", model)
    assert run.status == "step_cap" and run.findings == "Partial view."
    assert [s.action for s in run.steps] == ["market_data", "market_data", "final"]
    assert model.calls[-1][1]["properties"]["action"]["enum"] == ["final"]


async def test_a_garbled_reply_or_unknown_tool_is_shown_to_the_model_not_raised(ctx):
    model = ScriptedModel("not json at all", step("crystal_ball", "AAPL"), step("final", "Done."))
    run = await research(ctx, "AAPL", model)
    garbled, unknown, _ = run.steps
    assert "Not understood" in garbled.observation
    assert "No tool named 'crystal_ball'" in unknown.observation and "market_data" in unknown.observation
    assert run.status == "done"
    # The model saw both problems before answering.
    last_messages = model.calls[-1][0]
    assert "Not understood" in last_messages[-3]["content"] and "crystal_ball" in last_messages[-1]["content"]


async def test_a_failing_tool_is_an_observation(ctx):
    run = await research(ctx, "AAPL", ScriptedModel(step("market_data", "NOPE"), step("final", "Done.")))
    assert run.steps[0].observation.startswith("market_data failed: no listing 'NOPE'")


async def test_a_backend_that_cannot_answer_fails_the_run_but_keeps_its_steps(ctx):
    from oracle.research.llm import ModelError
    from oracle.services.errors import ModelUnavailable

    model = ScriptedModel(step("market_data"), ModelError("ollama is not reachable"))
    with pytest.raises(ModelUnavailable, match="not reachable"):
        await research(ctx, "AAPL", model)
    run = latest_research(ctx, "AAPL")
    assert run.status == "failed" and run.error == "ollama is not reachable"
    assert [s.action for s in run.steps] == ["market_data"]


async def test_a_run_already_going_elsewhere_is_refused(ctx):
    from oracle.services.errors import JobAlreadyRunning
    from oracle.services.jobs_service import single_flight

    with single_flight(ctx, "research", 60):
        with pytest.raises(JobAlreadyRunning, match="research is already running"):
            await research(ctx, "AAPL", ScriptedModel())
    assert latest_research(ctx, "AAPL") is None


async def test_market_data_reads_as_plain_figures_and_forgives_stray_quotes(ctx):
    from datetime import date

    from oracle.market.fundamentals import Fundamentals
    from oracle.store.fundamentals import FundamentalsRepo

    FundamentalsRepo(ctx.conn).save(Fundamentals("AAPL", date(2026, 9, 21), "yahoo", "USD", market_cap=4.96e12,
                                                 pe=38.9, roe=1.49, dividend_yield=0.004), "2026-09-21T20:00:00+00:00")
    run = await research(ctx, "AAPL", ScriptedModel(step("market_data", "AAPL'}"), step("final", "Done.")))
    observation = run.steps[0].observation
    assert "Apple Inc." in observation and "Change over 1 day: +5.0%" in observation
    assert "Market cap 4.96T USD" in observation and "P/E 38.90" in observation
    assert "Return on equity 149.0%" in observation and "Dividend yield 0.4%" in observation
