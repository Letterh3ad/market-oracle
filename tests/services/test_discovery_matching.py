"""A name matched to a company only by the start of its name is refused when the company's
business or home plainly is not what the question and its news are about. Fixtures from the
2026-09-24 question "what european companies are big in the semiconducter game", where "Olix"
(a UK chip startup) matched OliX Pharmaceuticals."""

from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.discovery_chat_service import ask
from oracle.services.discovery_service import discover
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.gdelt import FakeGdelt
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import ScriptedModel
from tests.fakes.web import FakeWeb
from tests.fakes.web_search import FakePages, FakeSearcher
from tests.fakes.yahoo import FakeYahoo, bar
from tests.services.test_discovery import TOYOTA, headlines, reasons, settings

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
QUESTION = "what european companies are big in the semiconducter game"
CHIPS = dict(sector="Information Technology", industry_group="Semiconductors & Semiconductor Equipment",
             industry="Semiconductors & Semiconductor Equipment")
OLIX = "OliX Pharmaceuticals,Inc"
UNIVERSE = [
    equity("226950.KQ", OLIX, "South Korea", "KOE", currency="KRW", market_cap="Micro Cap", sector="Health Care",
           industry_group="Pharmaceuticals, Biotechnology & Life Sciences"),
    equity("IFX.DE", "Infineon Technologies AG", "Germany", "GER", currency="EUR", market_cap="Large Cap", **CHIPS),
    equity("ASML", "ASML Holding N.V.", "Netherlands", "NMS", currency="USD", market_cap="Mega Cap", **CHIPS),
    equity("MEDX", "Medix", "Sweden", "STO", currency="SEK", market_cap="Small Cap", sector="Health Care"),
    equity("QTXS", "Quantix Systems Inc.", currency="USD", market_cap="Small Cap"),
    equity("7203.T", TOYOTA, "Japan", "JPX", currency="JPY", market_cap="Mega Cap",
           sector="Consumer Discretionary", industry_group="Automobiles & Components"),
]
# The live answer's three items, then one naming a true match.
HEADLINES = [
    "Samsung Electronics to develop semiconductor-focused AI model with Europe's top AI firm Mistral AI",
    "Olix raises Europe's largest chip round at $3.3B valuation",
    "Olix raises Europe’s largest chip round at $3.3B valuation",
    "Infineon lifts outlook as chip demand recovers",
]
QUERY = "European semiconductor companies list"


@pytest.fixture
def news():
    return FakeGoogleNews()


@pytest.fixture
def gdelt():
    return FakeGdelt()


@pytest.fixture
async def ctx(tmp_path, news, gdelt):
    yahoo = FakeYahoo()
    for row in UNIVERSE:
        yahoo.add(row["symbol"], "USD", "America/New_York", [bar("2026-09-22", 10.0), bar("2026-09-23", 11.0)])
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo, news, gdelt, FakePages())
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        yield c


def found(news, *titles):
    news.results[QUERY] = [(f"https://news.test/{i}", t, "Wire", datetime(2026, 9, 23, 20 - i, tzinfo=timezone.utc))
                           for i, t in enumerate(titles)]


def model(*companies, queries=(QUERY,)):
    m = ScriptedModel({"companies": [{"name": n, "why": "named in the news", "headlines": list(h)}
                                     for n, h in companies]})
    m.suggestions = {"queries": list(queries), "local": [], "market_caps": [], "exclude": []}
    return m


async def asked(ctx, *companies, question=QUESTION):
    return await ask(ctx, question, model=model(*companies), searcher=FakeSearcher())


def left_off(view):
    return {i.name: i.skipped for i in view.skipped}


async def test_olix_the_chip_startup_is_not_matched_to_a_drug_maker(ctx, news):
    found(news, *HEADLINES)
    view = await asked(ctx, ("Olix", [2, 3]), ("Infineon", [4]), ("Mistral AI", [1]))
    assert [line.item.symbol for line in view.shortlist] == ["IFX.DE"], "a true match still passes"
    why = left_off(view)["Olix"]
    assert why == (f"a different business: {OLIX} is in Pharmaceuticals, Biotechnology & Life Sciences, "
                   'but the question and its news are about "chip"')
    assert left_off(view)["Mistral AI"] == "not in the universe under that name"


async def test_an_exact_name_is_never_second_guessed(ctx, news):
    found(news, "Medix joins a chip alliance")
    view = await asked(ctx, ("Medix", [1]))
    assert [line.item.symbol for line in view.shortlist] == ["MEDX"]


async def test_a_ticker_is_never_second_guessed(ctx, news):
    found(news, "QTXS shares jump on chip order")
    view = await asked(ctx, ("QTXS", [1]))
    assert [line.item.symbol for line in view.shortlist] == ["QTXS"]


async def test_a_leading_name_in_the_right_business_passes(ctx, news):
    found(news, "ASML ships more lithography tools to chipmakers")
    view = await asked(ctx, ("ASML", [1]))
    assert [line.item.symbol for line in view.shortlist] == ["ASML"]


async def test_the_question_s_own_industry_words_count(ctx, news):
    found(news, "Olix raises a record round")
    view = await asked(ctx, ("Olix", [1]), question="which European semiconductor firms are growing?")
    assert "a different business" in left_off(view)["Olix"]


async def test_with_no_industry_words_a_leading_name_passes(ctx, news):
    found(news, "Olix raises a record round")
    view = await asked(ctx, ("Olix", [1]), question="who raised money this week?")
    assert [line.item.symbol for line in view.shortlist] == ["226950.KQ"]


@pytest.mark.parametrize(("title", "home"), [
    ("German startup Quantix raises a record round", "Germany"),
    ("Europe's Quantix raises a record round", "Europe"),
    ("UK-based Quantix raises a record round", "United Kingdom"),
])
async def test_when_the_business_cannot_tell_the_home_the_news_names_can(ctx, news, title, home):
    found(news, title)
    view = await asked(ctx, ("Quantix", [1]), question="who raised money this week?")
    assert left_off(view)["Quantix"] == (f"a different company: Quantix Systems Inc. is from United States, "
                                         f"but the news names {home}")


@pytest.mark.parametrize("title", ["Quantix raises a record round in Germany", "American firm Quantix raises"])
async def test_a_place_that_is_not_the_company_s_home_or_its_own_country_passes(ctx, news, title):
    found(news, title)
    view = await asked(ctx, ("Quantix", [1]), question="who raised money this week?")
    assert [line.item.symbol for line in view.shortlist] == ["QTXS"]


async def test_one_gdelt_search_per_answer_the_rest_on_the_web(ctx, news):
    found(news, *HEADLINES)
    m = model(("Infineon", [4]), queries=(QUERY, "Big European chip firms", "Major chip makers"))
    searcher = FakeSearcher()
    view = await ask(ctx, QUESTION, model=m, searcher=searcher)
    assert view.answer.searches == (
        f"Google News: {QUERY}", f"GDELT: {QUERY}", "Google News: Big European chip firms",
        "Google News: Major chip makers", f"the web: {QUERY}", "the web: Big European chip firms",
        "the web: Major chip makers")
    assert searcher.queries == [QUERY, "Big European chip firms", "Major chip makers"]


async def test_tick_discovery_refuses_a_mismatched_leading_name_too(ctx, news):
    watch(ctx, "7203.T")
    settings(ctx, per_tick=1, search_themes=False, search_movers=False)
    headlines(news, TOYOTA, "Olix and Toyota team up on chips")
    run = await discover(ctx, ScriptedModel({"companies": [{"name": "Olix", "relation": "partner",
                                                            "headlines": [1]}]}))
    assert run.added == () and reasons(run)["Olix"].startswith(f"a different business: {OLIX}")


async def test_a_full_plan_still_asks_gdelt_once(ctx, news):
    found(news, *HEADLINES)
    queries = (QUERY, "Big European chip firms", "Major chip makers", "Europe top semiconductor companies")
    view = await ask(ctx, QUESTION, model=model(("Infineon", [4]), queries=queries), searcher=FakeSearcher())
    tools = [s.split(":")[0] for s in view.answer.searches]
    assert tools.count("GDELT") == 1 and tools.count("Google News") == 4 and tools.count("the web") == 4
