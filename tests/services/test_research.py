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
from tests.fakes.sec import FakeSec
from tests.fakes.web import FakeWeb
from tests.fakes.web_search import FakePages, FakeSearcher
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)
HARDWARE = dict(sector="Information Technology", industry_group="Technology Hardware & Equipment")
UNIVERSE = [
    equity("AAPL", "Apple Inc.", currency="USD", market_cap="Mega Cap", **HARDWARE),
    equity("DELL", "Dell Technologies", currency="USD", market_cap="Large Cap", **HARDWARE),
    equity("HPQ", "HP Inc.", currency="USD", market_cap="Large Cap", **HARDWARE),
    equity("6758.T", "Sony Group", "Japan", "JPX", currency="JPY", market_cap="Large Cap", **HARDWARE),
    equity("LOGI", "Logitech", currency="USD", market_cap="Mid Cap", **HARDWARE),
    equity("SMCI", "Super Micro Computer", currency="USD", market_cap="Mid Cap", **HARDWARE),
    equity("SSUN.VI", "Samsung Electronics Co Ltd Sponsored GDR Pfd", "South Korea", "VIE", currency="USD",
           market_cap="Mega Cap", **HARDWARE),
    equity("MSFT", "Microsoft", currency="USD", market_cap="Mega Cap", sector="Information Technology",
           industry_group="Software & Services"),
]


@pytest.fixture
def news():
    return FakeGoogleNews()


@pytest.fixture
def gdelt():
    return FakeGdelt()


@pytest.fixture
def searcher():
    return FakeSearcher()


@pytest.fixture
def pages():
    return FakePages()


@pytest.fixture
def sec():
    return FakeSec()


@pytest.fixture
async def ctx(tmp_path, news, gdelt, pages, sec):
    yahoo = FakeYahoo()
    yahoo.add("AAPL", "USD", "America/New_York", [bar("2026-09-18", 100.0), bar("2026-09-21", 105.0)])
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo, news, gdelt, pages, sec)
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


def articles(run):
    """The run's evidence other than the stored-data item every report cites from."""
    return [e for e in run.evidence if e.outlet != "stored data"]


def loop_calls(model):
    return [c for c in model.calls if "action" in c[1].get("properties", {})]


def with_steps(ctx, n):
    from dataclasses import replace

    ctx.config = replace(ctx.config, llm=replace(ctx.config.llm, max_steps=n))


async def test_at_the_step_cap_the_run_is_made_to_answer_and_says_it_was_capped(ctx):
    with_steps(ctx, 2)
    model = ScriptedModel(step("market_data"), step("market_data"), step("final", "Partial view."))
    run = await research(ctx, "AAPL", model)
    assert run.status == "step_cap" and run.findings == "Partial view."
    assert [s.action for s in run.steps] == ["market_data", "market_data", "final"]
    assert loop_calls(model)[-1][1]["properties"]["action"]["enum"] == ["final"]


async def test_a_garbled_reply_or_unknown_tool_is_shown_to_the_model_not_raised(ctx):
    model = ScriptedModel("not json at all", step("crystal_ball", "AAPL"), step("final", "Done."))
    run = await research(ctx, "AAPL", model)
    garbled, unknown, _ = run.steps
    assert "Not understood" in garbled.observation
    assert "No tool named 'crystal_ball'" in unknown.observation and "market_data" in unknown.observation
    assert run.status == "done"
    # The model saw both problems before answering.
    last_messages = loop_calls(model)[-1][0]
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
    beat, slows = articles(run)
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
    assert [e.id for e in articles(run)] == [1]


async def test_a_citation_of_something_never_shown_is_dropped(ctx, news):
    news.results["Apple"] = [("https://reut.rs/1", "Apple beats estimates", "Reuters", utc("2026-09-20"))]
    run = await research(ctx, "AAPL", ScriptedModel(step("news_search", "Apple"),
                                                    step("final", "Beat estimates [a1]; sales doubled [a99].")))
    assert run.findings == "Beat estimates [a1]; sales doubled."
    assert [(e.id, e.cited) for e in articles(run)] == [(1, True)]


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
    assert [(e.url, e.cited) for e in articles(run)] == [("https://lemonde.fr/1", True)]
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
    assert "2026-09-18" in model.calls[1][0][1]["content"]
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
    assert [(e.id, e.cited) for e in articles(run)] == [(1, True)]


async def test_web_search_results_are_undated_evidence_with_a_snippet(ctx, searcher):
    from oracle.research.web import WebResult

    searcher.results["Apple supplier"] = [WebResult("https://www.tsmc.com/a", "TSMC and Apple", "Apple is TSMC's largest customer.")]
    model = ScriptedModel(step("web_search", "Apple supplier"), step("final", "Key supplier [a1]."))
    run = await research(ctx, "AAPL", model, searcher=searcher)
    assert run.steps[0].observation == "[a1] undated tsmc.com: TSMC and Apple | Apple is TSMC's largest customer."
    assert [(e.url, e.outlet, e.cited) for e in articles(run)] == [("https://www.tsmc.com/a", "tsmc.com", True)]


async def test_a_rate_limited_web_search_trips_its_breaker_and_then_is_not_called(ctx, searcher):
    from oracle.research.web import WebSearchFailed

    searcher.fail = WebSearchFailed("202 Ratelimit", rate_limited=True)
    model = ScriptedModel(*[step("web_search", "Apple")] * 4, step("final", "Nothing."))
    run = await research(ctx, "AAPL", model, searcher=searcher)
    assert "rate limited" in run.steps[0].observation
    assert "circuit open" in run.steps[3].observation and len(searcher.queries) == 3


async def test_a_page_is_read_as_plain_text_and_can_be_cited(ctx, pages):
    pages.pages["https://news.test/story"] = ("text/html; charset=utf-8", (
        "<html><head><title>Apple results</title><style>p{}</style></head><body><nav>Home | Markets</nav>"
        "<article><h1>Apple beats</h1><p>Revenue rose 8%.</p><script>track()</script></article>"
        "<footer>Cookies</footer></body></html>"))
    model = ScriptedModel(step("read_page", "https://news.test/story"), step("final", "Rose 8% [a1]."))
    run = await research(ctx, "AAPL", model)
    assert run.steps[0].observation == "[a1] news.test: Apple results\nApple beats\nRevenue rose 8%."
    assert [(e.title, e.cited) for e in articles(run)] == [("Apple results", True)]


@pytest.mark.parametrize("url, says", [
    ("file:///C:/secrets.txt", "only http and https"),
    ("http://localhost:11434/api/tags", "local addresses"),
    ("http://192.168.1.1/admin", "private and local network"),
    ("https://news.google.com/rss/articles/CBMi", "search its headline"),
    ("http://[bad", "not a valid address"),
])
async def test_pages_that_must_not_or_cannot_be_read_are_refused(ctx, url, says):
    run = await research(ctx, "AAPL", ScriptedModel(step("read_page", url), step("final", "Done.")))
    assert says in run.steps[0].observation


async def test_a_page_that_is_not_text_is_refused_and_a_long_one_is_marked_cut(ctx, pages):
    pages.pages["https://files.test/report.pdf"] = ("application/pdf", "%PDF-1.7")
    pages.pages["https://news.test/long"] = ("text/html", "<title>Long</title><p>" + "word " * 3000 + "</p>")
    model = ScriptedModel(step("read_page", "https://files.test/report.pdf"), step("read_page", "https://news.test/long"),
                          step("final", "Done."))
    pdf, long = (await research(ctx, "AAPL", model)).steps[:2]
    assert "not a web page (application/pdf)" in pdf.observation
    assert long.observation.endswith("[cut]") and len(long.observation) <= 1510


async def test_an_as_of_replay_has_no_web_tools_as_pages_carry_no_date(ctx, searcher):
    from datetime import date

    model = ScriptedModel(step("web_search", "Apple"), step("final", "Done."))
    run = await research(ctx, "AAPL", model, as_of=date(2026, 9, 18), searcher=searcher)
    assert "No tool named 'web_search'" in run.steps[0].observation and searcher.queries == []
    assert "read_page" not in model.calls[1][0][0]["content"]


async def test_a_page_built_by_scripts_still_gives_its_description(ctx, pages):
    pages.pages["https://app.test/news"] = ("text/html", (
        '<html><head><title>Newsroom</title><meta property="og:description" content="Official news from Apple.">'
        '</head><body><div id="root"></div><script>render()</script></body></html>'))
    run = await research(ctx, "AAPL", ScriptedModel(step("read_page", "https://app.test/news"), step("final", "Done.")))
    assert run.steps[0].observation == "[a1] app.test: Newsroom\nOfficial news from Apple."


async def test_a_full_page_of_web_results_fits_one_observation_uncut(ctx, searcher):
    from oracle.research.web import WebResult

    searcher.results["Apple"] = [WebResult(f"https://s{i}.test/{'p' * 80}", "T" * 200, "S" * 400) for i in range(10)]
    run = await research(ctx, "AAPL", ScriptedModel(step("web_search", "Apple"), step("final", "Done.")),
                         searcher=searcher)
    lines = run.steps[0].observation.splitlines()
    assert len(lines) == 5 and not lines[-1].endswith("[cut]") and lines[-1].endswith("S" * 99 + "…")


def with_contact(ctx, contact="Faye faye@example.com"):
    from dataclasses import replace

    ctx.config = replace(ctx.config, research=replace(ctx.config.research, contact=contact))


APPLE_CIK = 320193
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/320193"


def apple_filings(sec):
    sec.companies["AAPL"] = (APPLE_CIK, "Apple Inc.")
    sec.filings[APPLE_CIK] = [
        ("8-K", "2026-09-10", "0000320193-26-000090", "aapl-8k.htm", "Current report"),
        ("4", "2026-09-05", "0000320193-26-000089", "form4.xml", ""),
        ("10-Q", "2026-08-01", "0000320193-26-000077", "aapl-20260627.htm", "Quarterly report"),
    ]


async def test_filings_lists_a_us_companys_key_sec_filings_as_dated_evidence(ctx, sec):
    with_contact(ctx)
    apple_filings(sec)
    run = await research(ctx, "AAPL", ScriptedModel(step("filings"), step("final", "Filed a 10-Q [a2].")))
    assert run.steps[0].observation == (
        "[a1] 2026-09-10 SEC: 8-K Current report\n[a2] 2026-08-01 SEC: 10-Q Quarterly report")
    assert articles(run)[1].url == f"{ARCHIVE}/000032019326000077/aapl-20260627.htm" and articles(run)[1].cited
    assert all("faye@example.com" in r.headers["user-agent"] for r in sec.requests)


async def test_the_ticker_map_is_fetched_once_and_reused(ctx, sec):
    with_contact(ctx)
    apple_filings(sec)
    for _ in range(2):
        await research(ctx, "AAPL", ScriptedModel(step("filings"), step("final", "Done.")))
    assert [r.url.path for r in sec.requests].count("/files/company_tickers.json") == 1


@pytest.mark.parametrize("contact, input_, says", [
    ("Faye faye@example.com", "7203.T", "not a US listing"),
    ("Faye faye@example.com", "ZZZZ", "No SEC filer with ticker ZZZZ"),
    (None, "AAPL", "[research] contact"),
])
async def test_filings_answers_plainly_when_there_are_none_to_get(ctx, sec, contact, input_, says):
    with_contact(ctx, contact)
    apple_filings(sec)
    run = await research(ctx, "AAPL", ScriptedModel(step("filings", input_), step("final", "Done.")))
    assert says in run.steps[0].observation
    if input_ != "ZZZZ":
        assert sec.requests == []


async def test_a_filing_is_read_from_sec_with_the_contact_and_its_own_budget(ctx, sec):
    with_contact(ctx)
    url = f"{ARCHIVE}/000032019326000077/aapl-20260627.htm"
    sec.documents[url] = "<title>10-Q</title><p>Net sales rose 8%.</p>"
    run = await research(ctx, "AAPL", ScriptedModel(step("read_page", url), step("final", "Done.")))
    assert run.steps[0].observation.endswith("Net sales rose 8%.")
    assert "faye@example.com" in sec.requests[0].headers["user-agent"]


async def test_an_as_of_replay_sees_only_filings_made_by_then(ctx, sec):
    from datetime import date

    with_contact(ctx)
    apple_filings(sec)
    run = await research(ctx, "AAPL", ScriptedModel(step("filings"), step("final", "Done.")), as_of=date(2026, 9, 1))
    assert run.steps[0].observation == "[a1] 2026-08-01 SEC: 10-Q Quarterly report"


async def test_related_companies_are_universe_peers_with_the_reason_named(ctx, pages):
    run = await research(ctx, "AAPL", ScriptedModel(step("related_companies"), step("final", "Done.")))
    observation = run.steps[0].observation
    lines = observation.splitlines()
    assert lines[0] == "Apple Inc. (AAPL) is related to these by industry group Technology Hardware & Equipment:"
    assert {"DELL: Dell Technologies (Large Cap)", "6758.T: Sony Group (Large Cap)"} <= set(lines[1:])
    # Depositary receipt lines repeat a company already listed elsewhere.
    assert "Microsoft" not in observation and "GDR" not in observation and len(lines) == 6


async def test_related_companies_of_an_unknown_symbol_says_so(ctx):
    run = await research(ctx, "AAPL", ScriptedModel(step("related_companies", "NOPE"), step("final", "Done.")))
    assert "no listing 'NOPE'" in run.steps[0].observation


async def test_a_run_starts_from_seed_queries_and_the_models_suggestions(ctx):
    model = ScriptedModel(step("final", "Done."))
    model.suggestions = {"queries": ["Apple iPhone 18 demand", "apple SHARES", "", "x" * 150,
                                     "Apple tariffs", "Apple CEO John Ternus", "Apple services growth", "Apple AI"]}
    run = await research(ctx, "AAPL", model)
    assert run.queries == ("Apple shares", "Apple earnings outlook", "Apple iPhone 18 demand", "Apple tariffs",
                           "Apple CEO John Ternus", "Apple services growth")
    task = model.calls[1][0][1]["content"]
    assert "Start with these searches" in task and "- Apple CEO John Ternus" in task
    assert latest_research(ctx, "AAPL").queries == run.queries


async def test_a_listing_abroad_gets_a_query_in_its_language_and_the_model_is_asked_for_its_local_name(ctx):
    model = ScriptedModel(step("final", "Done."))
    run = await research(ctx, "6758.T", model)
    assert run.queries[:3] == ("Sony shares", "Sony earnings outlook", "Sony 株価")
    assert "Japanese" in model.calls[0][0][-1]["content"]


async def test_a_garbled_suggestion_reply_leaves_the_seed_queries(ctx):
    model = ScriptedModel(step("final", "Done."))
    model.suggestions = "no idea"
    run = await research(ctx, "AAPL", model)
    assert run.queries == ("Apple shares", "Apple earnings outlook")


async def test_citations_grouped_in_one_bracket_each_count(ctx, news):
    news.results["Apple"] = [("https://x.com/1", "One", "X", utc("2026-09-20")), ("https://x.com/2", "Two", "X", utc("2026-09-19"))]
    run = await research(ctx, "AAPL", ScriptedModel(step("news_search", "Apple"),
                                                    step("final", "Both agree [a1, a2, a9].")))
    assert run.findings == "Both agree [a1] [a2]."
    assert [(e.id, e.cited) for e in articles(run)] == [(1, True), (2, True)]


def report_script(news):
    news.results["Apple"] = [("https://reut.rs/1", "Apple beats estimates", "Reuters", utc("2026-09-20")),
                             ("https://bbc.in/2", "iPhone demand slows in China", "BBC", utc("2026-09-19"))]
    model = ScriptedModel(step("news_search", "Apple"), step("final", "Beat [a1]."))
    model.roles = {
        "fundamental_analyst": {"findings": [{"text": "Valuation is rich", "cites": ["a3"]},
                                             {"text": "Margins will double", "cites": []}]},
        "news_analyst": {"findings": [{"text": "Beat estimates", "cites": ["a1"]},
                                      {"text": "Sales doubled", "cites": ["a99"]}]},
        "bull": {"case": "Earnings momentum", "cites": ["a1"]},
        "bear": {"case": "Priced for perfection", "cites": ["a3", "a2"]},
        "risk": {"risks": [{"text": "China demand", "cites": ["a2"]}], "invalidated_if": "Next quarter misses"},
    }
    return model


async def test_the_roles_turn_the_evidence_into_a_report_of_cited_claims_only(ctx, news):
    from datetime import date

    from oracle.research.trace import Claim

    model = report_script(news)
    run = await research(ctx, "AAPL", model)
    report = run.report
    assert report.day == date(2026, 9, 21)
    assert report.findings == (Claim("Valuation is rich", (3,), "fundamental_analyst"),
                               Claim("Beat estimates", (1,), "news_analyst"))
    assert report.bull == Claim("Earnings momentum", (1,), "bull")
    assert report.bear == Claim("Priced for perfection", (3, 2), "bear")
    assert report.risks == (Claim("China demand", (2,), "risk"),) and report.invalidated_if == "Next quarter misses"
    data = next(e for e in run.evidence if e.id == 3)
    assert data.outlet == "stored data" and data.published.date() == date(2026, 9, 21)
    assert all(e.cited for e in run.evidence)
    assert latest_research(ctx, "AAPL").report == report


async def test_every_role_reads_the_dated_evidence_and_the_stored_data(ctx, news):
    model = report_script(news)
    await research(ctx, "AAPL", model)
    roles = [c for c in model.calls if c[0][0]["content"].startswith("Role: ")]
    assert [c[0][0]["content"].split(".")[0] for c in roles] == [
        "Role: fundamental_analyst", "Role: technical_analyst", "Role: news_analyst", "Role: bull", "Role: bear",
        "Role: risk"]
    digest = roles[0][0][1]["content"]
    assert "[a1] 2026-09-20 Reuters: Apple beats estimates" in digest
    assert "[a3] 2026-09-21 stored data:" in digest and "Last close 105.00 USD" in digest
    assert "Beat estimates" in roles[3][0][1]["content"]  # the bull reads the analysts' findings


async def test_a_case_with_no_valid_citation_is_left_out(ctx, news):
    model = report_script(news)
    model.roles["bull"] = {"case": "To the moon", "cites": ["a42"]}
    model.roles["risk"] = "garbled"
    report = (await research(ctx, "AAPL", model)).report
    assert report.bull is None and report.bear is not None and report.risks == () and report.invalidated_if is None


async def test_research_never_changes_the_traded_lean(ctx, news):
    from datetime import date

    from oracle.analysts.signals import AnalystSignal
    from oracle.broker.rules import Lean
    from oracle.services.analysis_service import latest_lean
    from oracle.store.leans import LeanRepo
    from oracle.store.signals import SignalRepo

    at = "2026-09-21T20:00:00+00:00"
    SignalRepo(ctx.conn).save([AnalystSignal("technicals", "AAPL", date(2026, 9, 21), 0.5, 0.7, "Momentum is up.", ())], at)
    LeanRepo(ctx.conn).save([Lean("AAPL", date(2026, 9, 21), "hold", 0.6, "baseline")], "from technicals", at)
    before = latest_lean(ctx, "AAPL")
    model = report_script(news)
    model.roles["bull"] = {"case": "Strong buy now", "cites": ["a1"]}
    await research(ctx, "AAPL", model)
    assert latest_lean(ctx, "AAPL") == before
    digest = [c for c in model.calls if c[0][0]["content"].startswith("Role: technical_analyst")][0][0][1]["content"]
    assert "technicals: bullish (Momentum is up.)" in digest


async def test_report_history_lists_each_run_newest_first(ctx, news):
    from oracle.services.research_service import report_history

    for case in ("First view", "Second view"):
        model = report_script(news)
        model.roles["bull"] = {"case": case, "cites": ["a1"]}
        await research(ctx, "AAPL", model)
    assert [r.report.bull.text for r in report_history(ctx, "AAPL")] == ["Second view", "First view"]


def test_the_configured_backend_decides_which_model_runs(ctx):
    from dataclasses import replace

    from oracle.services.research_service import configured_model

    assert (configured_model(ctx).name, configured_model(ctx).model) == ("ollama", "qwen3:4b-instruct-2507-q8_0")
    ctx.config = replace(ctx.config, llm=replace(ctx.config.llm, backend="claude"))
    assert (configured_model(ctx).name, configured_model(ctx).model) == ("claude", "claude-opus-5")


async def test_a_redirect_into_this_machine_or_the_lan_is_refused_at_each_hop(ctx, pages):
    pages.pages["https://evil.test/r"] = ("redirect", "http://127.0.0.1:11434/api/tags")
    pages.pages["https://news.test/old"] = ("redirect", "https://news.test/new")
    pages.pages["https://news.test/new"] = ("text/html", "<title>Moved</title><p>Here now.</p>")
    model = ScriptedModel(step("read_page", "https://evil.test/r"), step("read_page", "https://news.test/old"),
                          step("final", "Done."))
    evil, moved = (await research(ctx, "AAPL", model)).steps[:2]
    assert "private and local network addresses cannot be read" in evil.observation
    assert moved.observation.endswith("Here now.")
    assert all(r.url.host != "127.0.0.1" for r in pages.requests)


async def test_a_redirect_that_names_no_target_is_an_error_not_a_page(ctx, pages):
    pages.pages["https://news.test/lost"] = ("redirect", "")
    model = ScriptedModel(step("read_page", "https://news.test/lost"), step("final", "Done."))
    [lost] = (await research(ctx, "AAPL", model)).steps[:1]
    assert "without saying where to go" in lost.observation


async def test_the_sec_contact_is_not_carried_to_another_host_by_a_redirect(ctx, sec, pages):
    with_contact(ctx)
    url = f"{ARCHIVE}/000032019326000077/moved.htm"
    sec.documents[url] = None
    pages.pages["https://mirror.test/doc"] = ("text/html", "<p>Filing text.</p>")

    def moved(request, original=sec.handle):
        import httpx

        if str(request.url) == url:
            return httpx.Response(301, headers={"Location": "https://mirror.test/doc"})
        return original(request)

    sec.handle = moved
    run = await research(ctx, "AAPL", ScriptedModel(step("read_page", url), step("final", "Done.")))
    assert run.steps[0].observation.endswith("Filing text.")
    [mirror] = [r for r in pages.requests if r.url.host == "mirror.test"]
    assert "faye@example.com" not in mirror.headers.get("user-agent", "")


async def test_an_unexpected_failure_still_marks_the_run_failed(ctx):
    model = ScriptedModel(step("market_data"), RuntimeError("disk full"))
    with pytest.raises(RuntimeError):
        await research(ctx, "AAPL", model)
    run = latest_research(ctx, "AAPL")
    assert run.status == "failed" and "disk full" in run.error


async def test_a_model_that_fell_back_is_named_in_the_run(ctx):
    model = ScriptedModel(step("final", "Done."))
    model.falls_back_to = "claude-opus-4-8"
    run = await research(ctx, "AAPL", model)
    assert run.model == "script" and run.fell_back_to == ("claude-opus-4-8",)


async def test_a_fallback_is_recorded_on_a_failed_run_too(ctx):
    model = ScriptedModel(step("market_data"), RuntimeError("disk full"))
    model.falls_back_to = "claude-opus-4-8"
    with pytest.raises(RuntimeError):
        await research(ctx, "AAPL", model)
    assert latest_research(ctx, "AAPL").fell_back_to == ("claude-opus-4-8",)


async def test_a_reused_model_starts_each_run_without_the_last_ones_fallback(ctx):
    model = ScriptedModel(step("final", "Done."))
    model.falls_back_to = "claude-opus-4-8"
    await research(ctx, "AAPL", model)
    model.falls_back_to = None
    model.replies = [step("final", "Again.")]
    assert (await research(ctx, "AAPL", model)).fell_back_to == ()


@pytest.mark.parametrize("stage", ["profile", "save_report"])
async def test_a_failure_outside_the_loop_still_marks_the_run_failed(ctx, monkeypatch, stage):
    from oracle.store.research import ResearchRepo
    from oracle.store.universe import UniverseRepo

    def boom(*_args):
        raise RuntimeError("disk full")

    monkeypatch.setattr(UniverseRepo if stage == "profile" else ResearchRepo, stage, boom)
    with pytest.raises(RuntimeError):
        await research(ctx, "AAPL", ScriptedModel(step("final", "Done.")))
    run = latest_research(ctx, "AAPL")
    assert run.status == "failed" and "disk full" in run.error and run.report is None


async def test_a_cancelled_run_says_so_plainly(ctx):
    import asyncio

    with pytest.raises(asyncio.CancelledError):
        await research(ctx, "AAPL", ScriptedModel(step("market_data"), asyncio.CancelledError()))
    run = latest_research(ctx, "AAPL")
    assert run.status == "failed" and run.error == "cancelled"


async def test_a_run_that_cannot_be_marked_failed_still_raises_the_original_error(ctx, monkeypatch):
    from oracle.store.research import ResearchRepo

    def locked(*_args):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ResearchRepo, "finish", locked)
    with pytest.raises(RuntimeError, match="disk full") as caught:
        await research(ctx, "AAPL", ScriptedModel(step("market_data"), RuntimeError("disk full")))
    assert "database is locked" in "".join(caught.value.__notes__)


async def test_the_trace_itself_says_when_the_step_limit_was_hit(ctx):
    with_steps(ctx, 1)
    run = await research(ctx, "AAPL", ScriptedModel(step("market_data"), step("final", "Partial.")))
    assert run.steps[-1].observation == "Stopped at the 1-step limit; the findings were asked for then."


async def test_the_stored_data_a_run_read_is_kept_with_it(ctx):
    run = await research(ctx, "AAPL", ScriptedModel(step("final", "Done.")))
    [data] = [e for e in run.evidence if e.outlet == "stored data"]
    assert "Last close 105.00 USD" in data.body


async def test_each_article_keeps_the_query_that_first_found_it(ctx, news):
    news.results["Apple earnings"] = [("https://reut.rs/1", "Apple beats estimates", "Reuters", utc("2026-09-20"))]
    run = await research(ctx, "AAPL", ScriptedModel(step("news_search", "Apple earnings"), step("final", "Done.")))
    assert articles(run)[0].query == "Apple earnings"
