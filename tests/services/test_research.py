from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.prices_service import update_listing
from oracle.services.research_service import latest_research, research
from oracle.services.universe_service import refresh_universe
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.gdelt import FakeGdelt
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import ScriptedModel, step
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def news():
    return FakeGoogleNews()


@pytest.fixture
def gdelt():
    return FakeGdelt()


@pytest.fixture
async def ctx(tmp_path, news, gdelt):
    yahoo = FakeYahoo()
    yahoo.add("AAPL", "USD", "America/New_York", [bar("2026-09-18", 100.0), bar("2026-09-21", 105.0)])
    web = FakeWeb(FakeFinanceDatabase([equity("AAPL", "Apple Inc.", currency="USD")]), yahoo, news, gdelt)
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


def utc(day, hour=12):
    return datetime.fromisoformat(f"{day}T{hour:02}:00:00+00:00")


async def test_news_search_shows_dated_articles_the_findings_cite_by_id(ctx, news):
    news.results["Apple earnings"] = [
        ("https://reut.rs/1", "Apple beats estimates", "Reuters", utc("2026-09-20")),
        ("https://bbc.in/2", "iPhone demand slows in China", "BBC", utc("2026-09-19")),
    ]
    model = ScriptedModel(step("news_search", "Apple earnings"), step("final", "Beat estimates [a1]."))
    run = await research(ctx, "AAPL", model)
    observation = run.steps[0].observation
    assert "[a1] 2026-09-20 Reuters: Apple beats estimates" in observation
    assert "[a2] 2026-09-19 BBC: iPhone demand slows in China" in observation
    beat, slows = run.evidence
    assert (beat.id, beat.url, beat.outlet, beat.cited) == (1, "https://reut.rs/1", "Reuters", True)
    assert (slows.title, slows.published, slows.cited) == ("iPhone demand slows in China", utc("2026-09-19"), False)
    assert latest_research(ctx, "AAPL").evidence == run.evidence


async def test_an_article_found_twice_is_stored_once_and_keeps_its_id(ctx, news):
    story = ("https://reut.rs/1", "Apple beats estimates", "Reuters", utc("2026-09-20"))
    news.results["Apple"] = [story]
    news.results["AAPL results"] = [story]
    run = await research(ctx, "AAPL", ScriptedModel(step("news_search", "Apple"), step("news_search", "AAPL results"),
                                                    step("final", "Done [a1].")))
    assert run.steps[0].observation == run.steps[1].observation == "[a1] 2026-09-20 Reuters: Apple beats estimates"
    assert [e.id for e in run.evidence] == [1]


async def test_a_citation_of_something_never_shown_is_dropped(ctx, news):
    news.results["Apple"] = [("https://reut.rs/1", "Apple beats estimates", "Reuters", utc("2026-09-20"))]
    run = await research(ctx, "AAPL", ScriptedModel(step("news_search", "Apple"),
                                                    step("final", "Beat estimates [a1]; sales doubled [a99].")))
    assert run.findings == "Beat estimates [a1]; sales doubled."
    assert [(e.id, e.cited) for e in run.evidence] == [(1, True)]


@pytest.mark.parametrize("status, says", [(403, "HTTP 403"), (503, "news search unavailable")])
async def test_a_failing_news_source_is_an_observation_and_the_run_goes_on(ctx, news, status, says):
    news.status = status
    run = await research(ctx, "AAPL", ScriptedModel(step("news_search", "Apple"), step("final", "No news.")))
    assert says in run.steps[0].observation and run.status == "done"


async def test_only_the_newest_eight_are_shown_with_long_titles_cut(ctx, news):
    news.results["Apple"] = [(f"https://x.com/{d}", f"Story {d}", "X", utc(f"2026-09-{d:02}")) for d in range(1, 11)]
    news.results["Apple"].append(("https://x.com/old", "L" * 300, "Y", None))
    run = await research(ctx, "AAPL", ScriptedModel(step("news_search", "Apple"), step("final", "Done.")))
    lines = run.steps[0].observation.splitlines()
    assert len(lines) == 8 and "2026-09-10 X: Story 10" in lines[0] and "2026-09-03" in lines[-1]
    news.results["Apple"] = [("https://x.com/old", "L" * 300, "Y", None)]
    run = await research(ctx, "AAPL", ScriptedModel(step("news_search", "Apple"), step("final", "Done.")))
    assert run.steps[0].observation.endswith("undated Y: " + "L" * 139 + "…")


async def test_gdelt_search_shows_worldwide_articles_like_any_news(ctx, gdelt):
    gdelt.results["Apple"] = [("https://lemonde.fr/1", "Apple dévoile un iPhone pliable", "lemonde.fr", utc("2026-09-20"))]
    run = await research(ctx, "AAPL", ScriptedModel(step("gdelt_search", "Apple"), step("final", "Foldable [a1].")))
    assert run.steps[0].observation == "[a1] 2026-09-20 lemonde.fr: Apple dévoile un iPhone pliable"
    assert [(e.url, e.cited) for e in run.evidence] == [("https://lemonde.fr/1", True)]
    assert gdelt.params[0]["mode"].lower() == "artlist" and gdelt.params[0]["format"] == "json"


async def test_a_gdelt_complaint_about_the_query_is_shown_to_the_model(ctx, gdelt):
    gdelt.error_text = "Your search contained phrases that were too short."
    run = await research(ctx, "AAPL", ScriptedModel(step("gdelt_search", "AI"), step("final", "Done.")))
    assert "too short" in run.steps[0].observation and run.status == "done"


async def test_as_of_a_date_nothing_published_later_is_seen_even_in_market_data(ctx, news, gdelt):
    from datetime import date

    news.results["Apple before:2026-09-19"] = [
        ("https://x.com/early", "Early story", "X", utc("2026-09-18", 23)),
        ("https://x.com/late", "Leaked later story", "X", utc("2026-09-19", 1)),
        ("https://x.com/undated", "Undated story", "X", None),
    ]
    gdelt.results["Apple"] = [("https://y.com/late", "Later GDELT story", "y.com", utc("2026-09-20"))]
    model = ScriptedModel(step("news_search", "Apple"), step("gdelt_search", "Apple"), step("market_data"),
                          step("final", "Done."))
    run = await research(ctx, "AAPL", model, as_of=date(2026, 9, 18))
    google, world, market, _ = run.steps
    assert google.observation == "[a1] 2026-09-18 X: Early story"
    assert world.observation == "No articles found for 'Apple'."
    assert gdelt.params[0]["enddatetime"] == "20260918235959"
    assert "Last close 100.00 USD on 2026-09-18" in market.observation and "105" not in market.observation
    assert "2026-09-18" in model.calls[0][0][1]["content"]
    assert run.as_of == date(2026, 9, 18) and latest_research(ctx, "AAPL").as_of == date(2026, 9, 18)


async def test_an_as_of_date_in_the_future_is_refused(ctx):
    from datetime import date

    from oracle.services.errors import InvalidQuery

    with pytest.raises(InvalidQuery, match="future"):
        await research(ctx, "AAPL", ScriptedModel(), as_of=date(2026, 9, 22))


async def test_citations_written_in_round_brackets_still_count(ctx, news):
    news.results["Apple"] = [("https://reut.rs/1", "Apple beats estimates", "Reuters", utc("2026-09-20"))]
    run = await research(ctx, "AAPL", ScriptedModel(step("news_search", "Apple"),
                                                    step("final", "Beat estimates (a1); grew (a7).")))
    assert run.findings == "Beat estimates [a1]; grew."
    assert [(e.id, e.cited) for e in run.evidence] == [(1, True)]
