"""List pages: a "top 10" article or a Wikipedia "List of ..." page read for a question keeps the
companies it lists, so the shortlist can name companies no headline did."""

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from oracle.config import Config
from oracle.research.trace import Evidence
from oracle.research.web import WebResult
from oracle.services.context import open_context
from oracle.services.discovery_chat_service import PLAN, ask
from oracle.services.research_tools.reading import list_page, pick
from oracle.services.universe_service import refresh_universe
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.gdelt import FakeGdelt
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import ScriptedModel
from tests.fakes.web import FakeWeb
from tests.fakes.web_search import FakePages, FakeSearcher
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent.parent / "fixtures" / "list_pages"
TOP10 = "https://startups.test/semiconductor-manufacturing-companies-in-europe/"
TOP10_TITLE = "Top 10 Semiconductor Manufacturing Companies in Europe 2025"
WIKI = "https://en.wikipedia.org/wiki/List_of_largest_technology_companies_by_revenue"
WIKI_TITLE = "List of largest technology companies by revenue - Wikipedia"
QUERY = "European semiconductor companies list"
CHIPS = "which European chipmakers could gain from the EU Chips Act?"
SEMI = dict(sector="Information Technology", industry_group="Semiconductors & Semiconductor Equipment",
            industry="Semiconductors & Semiconductor Equipment")
UNIVERSE = [
    equity("ASML.AS", "ASML Holding N.V.", "Netherlands", "AMS", currency="EUR", market_cap="Mega Cap", **SEMI),
    equity("NXPI", "NXP Semiconductors N.V.", "Netherlands", currency="USD", market_cap="Large Cap", **SEMI),
    equity("AIXA.DE", "AIXTRON SE", "Germany", "GER", currency="EUR", market_cap="Mid Cap", **SEMI),
    equity("SOI.PA", "Soitec SA", "France", "PAR", currency="EUR", market_cap="Mid Cap", **SEMI),
    equity("IFX.DE", "Infineon Technologies AG", "Germany", "GER", currency="EUR", market_cap="Large Cap", **SEMI),
    equity("005930.KS", "Samsung Electronics Co., Ltd.", "South Korea", "KSC", currency="KRW", market_cap="Mega Cap",
           **SEMI),
    equity("DTE.DE", "Deutsche Telekom AG", "Germany", "GER", currency="EUR", market_cap="Mega Cap",
           sector="Communication Services", industry_group="Telecommunication Services",
           industry="Diversified Telecommunication Services"),
]
# Newer than the list page and from as many outlets as there are reading slots, none naming a listed company.
HEADLINES = ["EU Chips Act money flows to new fabs", "Brussels weighs a second Chips Act",
             "Chip subsidies race heats up in Europe", "Europe's share of chip output stalls",
             "Chipmakers lobby for faster permits"]
PAGE_COMPANIES = ["ASML", "NXP Semiconductors", "SOITEC", "Aixtron", "X- Fab", "Infineon", "STMicroelectronics",
                  "Technoprobe", "BE Semiconductor", "Melexis"]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ListReader(ScriptedModel):
    """Plans one search, notes a list page with the companies it names, and names the answer from
    the item holding a "Lists:" line."""

    def __init__(self, listed: list[str], named: list[str]) -> None:
        super().__init__()
        self.suggestions = {"queries": [QUERY], "local": [], "market_caps": [], "exclude": []}
        self.roles = {"note_taker": self._note}
        self.noted: list[str] = []
        self.naming: str | None = None
        self._listed = listed
        self._named = named

    def _note(self, messages):
        self.noted.append(messages[1]["content"])
        return {"about_company": True, "facts": [], "companies": self._listed}

    async def reply(self, messages, schema):
        if messages[0]["content"].startswith("You answer an investor's question"):
            self.calls.append((messages, schema))
            self.naming = messages[1]["content"]
            [n] = [int(m.group(1)) for m in re.finditer(r"^\[(\d+)\][^\[]*?Lists:", self.naming, re.M | re.S)]
            return {"companies": [{"name": c, "why": "On a list of them", "headlines": [n]} for c in self._named]}
        return await super().reply(messages, schema)


@pytest.fixture
def news():
    n = FakeGoogleNews()
    n.results[QUERY] = [(f"https://news{i}.test/{i}", t, f"Outlet {i}", datetime(2026, 9, 23, 20 - i,
                                                                               tzinfo=timezone.utc))
                        for i, t in enumerate(HEADLINES)]
    return n


@pytest.fixture
def pages():
    p = FakePages()
    p.pages[TOP10] = ("text/html", fixture("top10-european-semiconductor.html"))
    p.pages[WIKI] = ("text/html", fixture("wikipedia-largest-tech-companies.html"))
    return p


@pytest.fixture
def web(news, pages):
    y = FakeYahoo()
    for row in UNIVERSE:
        y.add(row["symbol"], "EUR", "Europe/Berlin", [bar("2026-09-22", 10.0), bar("2026-09-23", 11.0)])
    return FakeWeb(FakeFinanceDatabase(UNIVERSE), y, news, FakeGdelt(), pages)


@pytest.fixture
async def ctx(tmp_path, web):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        yield c


def searcher(url: str, title: str) -> FakeSearcher:
    s = FakeSearcher()
    s.results[QUERY] = [WebResult(url, title, "")]
    return s


async def test_a_top_10_article_names_companies_no_headline_did(ctx):
    m = ListReader(PAGE_COMPANIES, ["ASML", "NXP Semiconductors", "Aixtron", "SOITEC", "Infineon", "Intel"])
    view = await ask(ctx, CHIPS, model=m, searcher=searcher(TOP10, TOP10_TITLE))
    [noted] = m.noted
    assert TOP10_TITLE in noted.splitlines()[0], "read ahead of five newer headlines"
    assert "ASML, NXP Semiconductors, SOITEC" in noted, "the page's own text"
    assert ("Lists: ASML; NXP Semiconductors; SOITEC; Aixtron; X- Fab; Infineon; STMicroelectronics; Technoprobe; "
            "BE Semiconductor") in m.naming, "Melexis is not how the page spells it"
    assert {line.item.symbol for line in view.shortlist} == {"ASML.AS", "NXPI", "AIXA.DE", "SOI.PA", "IFX.DE"}
    assert all(line.evidence[0].url == TOP10 for line in view.shortlist)
    assert view.skipped == (), "Intel is on no item, so it is dropped before the screen"
    assert f'9 companies from the list page "{TOP10_TITLE}"' in view.answer.notes


async def test_a_wikipedia_list_page_is_read_before_newer_news(ctx):
    m = ListReader(["Samsung Electronics", "Deutsche Telekom", "Nvidia", "Apple", "Tesla"],
                   ["Samsung Electronics", "Deutsche Telekom"])
    view = await ask(ctx, "which are the biggest technology companies?", model=m, searcher=searcher(WIKI, WIKI_TITLE))
    assert len(m.noted) == 1 and "Samsung Electronics" in m.noted[0]
    assert "Lists: Samsung Electronics; Deutsche Telekom; Nvidia; Apple" in m.naming and "Tesla" not in m.naming
    assert {line.item.symbol for line in view.shortlist} == {"005930.KS", "DTE.DE"}


async def test_the_listed_names_still_pass_the_shortlist_checks(ctx):
    m = ListReader(PAGE_COMPANIES, ["ASML", "Infineon"])
    m.suggestions["market_caps"] = ["Mega Cap"]
    view = await ask(ctx, "which big European chipmakers?", model=m, searcher=searcher(TOP10, TOP10_TITLE))
    assert [line.item.symbol for line in view.shortlist] == ["ASML.AS"]
    assert {i.name: i.skipped for i in view.skipped} == {"Infineon": "not the size asked for (Large Cap)"}


def test_the_plan_may_ask_for_a_list_page():
    assert '"list of' in PLAN


@pytest.mark.parametrize("title,url", [
    (TOP10_TITLE, TOP10), (WIKI_TITLE, WIKI), ("15 Biggest Semiconductor Companies and Suppliers in Europe", "x"),
    ("Europe's largest battery makers ranked", "https://a.test/b"), ("Chips", "https://en.wikipedia.org/wiki/List_of_chips"),
    ("Top European semiconductor companies by market cap 2026", "https://s.test/1"),
    ("The 12 leading European chipmakers", "https://s.test/1"),
])
def test_list_pages_are_known_by_title_or_address(title, url):
    assert list_page(Evidence(1, url, title, None, None, False))


@pytest.mark.parametrize("title,url", [
    ("Siemens Energy tops estimates", "https://n.test/1"), ("ASML top executive leaves", "https://n.test/1"),
    ("Infineon lists new bonds", "https://n.test/1"), ("Chip stocks at a top", "https://n.test/1"),
    ("Top 10 chipmakers", "https://n.test/top10.pdf"), ("Top 5 takeaways from ASML earnings", "https://n.test/1"),
    ("Nvidia ranked most valuable brand", "https://n.test/1"), ("Chipmakers ranked by growth", "https://n.test/1"),
    ("ASML ranking slips", "https://n.test/1"), ("Top 3 risks for Infineon", "https://n.test/1"),
])
def test_news_and_pdfs_are_no_list_page(title, url):
    assert not list_page(Evidence(1, url, title, None, None, False))


@pytest.mark.parametrize("title", [TOP10_TITLE, "3 best AI stocks to buy now", "List of chipmakers",
                                   "Top 5 takeaways from ASML earnings"])
def test_a_dated_headline_or_stored_data_is_never_a_list_page(title):
    """Only an undated web result can take a list page's slot: a list-like headline would push news aside."""
    assert not list_page(Evidence(1, "https://n.test/1", title, "Reuters", NOW, False))
    assert not list_page(Evidence(1, "https://n.test/1", title, None, None, False, body="stored"))


def test_list_pages_take_the_first_reading_slots():
    news = [Evidence(i, f"https://n{i}.test/{i}", f"Story {i}", f"Outlet {i}",
                     datetime(2026, 9, 23, i, tzinfo=timezone.utc), False) for i in range(1, 6)]
    lists = [Evidence(10 + i, f"https://l{i}.test/", f"Top 10 chipmakers {i}", None, None, False) for i in range(3)]
    picked = pick(news + lists, set(), 4, first=[e.id for e in lists])
    assert [e.id for e in picked] == [10, 11, 1, 2], "two slots at most, so news is still read"
