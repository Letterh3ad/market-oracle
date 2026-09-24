"""The universe answers "which companies" questions: the plan's industry, region and size words
screen the universe, largest first, on each company's home line, and the companies found join
the news mentions in the shortlist. From the 2026-09-24 trigger: "European semiconductor, mid cap
and up" named 11 companies from headlines and matched 1."""

from datetime import date, datetime, timezone

import pytest

from oracle.config import Config
from oracle.services import discovery_chat_service
from oracle.services.context import open_context
from oracle.services.discovery_chat_service import ask, set_auto
from oracle.services.universe_screen import Filters, plan_filters, screen_universe
from oracle.store.health import HealthRepo
from oracle.store.meta import MetaRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.listings import Listing
from oracle.universe.regions import REGIONS
from tests.fakes.clock import FakeClock
from tests.fakes.gdelt import FakeGdelt
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import ScriptedModel
from tests.fakes.web import FakeWeb
from tests.fakes.web_search import FakePages, FakeSearcher
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
IT, CHIPS = "Information Technology", "Semiconductors & Semiconductor Equipment"
MID_AND_UP = ("Mega Cap", "Large Cap", "Mid Cap")
QUESTION = "European semiconductor companies, mid cap and up"


def company(key, name, country, cap, lines, sector=IT, group=CHIPS, industry=CHIPS, isin=None):
    """Lines are (symbol, exchange, currency[, the line's own name]); the first is the primary."""
    return [Listing(symbol, (own or [name])[0], "equity", country, exchange, isin=isin, currency=currency,
                    sector=sector, industry_group=group, industry=industry, market_cap=cap, company_key=key,
                    is_primary=n == 0)
            for n, (symbol, exchange, currency, *own) in enumerate(lines)]


DEFENCE = dict(sector="Industrials", group="Capital Goods", industry="Aerospace & Defense")
CARS = dict(sector="Consumer Discretionary", group="Automobiles & Components", industry="Automobiles")
UNIVERSE = [
    *company("asml", "ASML Holding N.V.", "Netherlands", "Mega Cap", [("ASML.AS", "AMS", "EUR"),
                                                                      ("ASML", "NMS", "USD")]),
    *company("ifx", "Infineon Technologies AG", "Germany", "Large Cap", [("IFX.DE", "GER", "EUR"),
                                                                         ("IFNNY", "PNK", "USD")]),
    # The universe's primary is the New York line; the home line is in Paris.
    *company("stm", "STMicroelectronics N.V.", "Switzerland", "Large Cap", [("STM", "NYQ", "USD"),
                                                                            ("STM.PA", "PAR", "EUR")]),
    *company("aixa", "AIXTRON SE", "Germany", "Mid Cap", [("AIXA.DE", "GER", "EUR")]),
    *company("besi", "BE Semiconductor Industries N.V.", "Netherlands", "Mid Cap", [("BESI.AS", "AMS", "EUR")]),
    *company("soi", "Soitec S.A.", "France", "Mid Cap", [("SOI.PA", "PAR", "EUR")]),
    *company("tiny", "Tinychip AG", "Germany", "Small Cap", [("TINY.DE", "GER", "EUR")]),
    *company("nvda", "NVIDIA Corporation", "United States", "Mega Cap", [("NVDA", "NMS", "USD")]),
    *company("otc", "Chipco GmbH", "Germany", "Large Cap", [("CHPCF", "PNK", "USD")]),
    *company("dead", "Deadchip N.V.", "Netherlands", "Large Cap", [("DEAD.AS", "AMS", "EUR")]),
    *company("adr", "Waferco plc GDR", "United Kingdom", "Large Cap",
             [("WAFR.L", "LSE", "GBp")]),
    # Every line says United States: the universe's country is wrong, its home line is not.
    *company("rhm", "Rheinmetall AG", "United States", "Large Cap", [("RNMBF", "PNK", "USD"),
                                                                     ("RHM.DE", "GER", "EUR")], **DEFENCE),
    *company("vow", "Volkswagen AG", "Germany", "Large Cap", [("VOW3.DE", "GER", "EUR")], **CARS),
    *company("tsla", "Tesla, Inc.", "United States", "Mega Cap", [("TSLA", "NMS", "USD"),
                                                                  ("TSLA.MI", "MIL", "EUR")], **CARS),
    # Home in Tokyo, with a New York line; the universe keeps its Sao Paulo receipt as a company of its own.
    *company("toyota", "Toyota Motor Corporation", "Japan", "Mega Cap", [("7203.T", "JPX", "JPY"),
                                                                         ("TM", "NYQ", "USD")], **CARS),
    *company("tmco", "TOYOTA MOTOR  DRN", "Japan", "Mega Cap", [("TMCO34.SA", "SAO", "BRL")], **CARS),
    # Listed only in New York: no line at home.
    *company("nio", "NIO Inc.", "China", "Large Cap", [("NIO", "NYQ", "USD")], **CARS),
    # Its only line at home is a receipt.
    *company("abrs", "Autobras S.A.", "Brazil", "Large Cap", [("ABRS", "NYQ", "USD"),
                                                              ("ABRS34.SA", "SAO", "BRL", "AUTOBRAS  DRN")], **CARS),
    *company("olix", "OliX Pharmaceuticals,Inc", "South Korea", "Micro Cap", [("226950.KQ", "KOE", "KRW")],
             sector="Health Care", group="Pharmaceuticals, Biotechnology & Life Sciences",
             industry="Pharmaceuticals"),
]
CHIP_LINES = ["ASML.AS", "IFX.DE", "STM.PA", "AIXA.DE", "BESI.AS", "SOI.PA"]
DAYS = [date(2026, 9, d) for d in (21, 22, 23)]


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    for s in {x.symbol for x in UNIVERSE}:
        y.add(s, "EUR", "Europe/Berlin", [bar(d.isoformat(), 10.0) for d in DAYS])
    return y


@pytest.fixture
def news():
    n = FakeGoogleNews()
    n.results["european chipmakers"] = [
        ("https://news.test/1", "Infineon opens its Dresden fab", "Wire", datetime(2026, 9, 23, 9, tzinfo=timezone.utc)),
        ("https://news.test/2", "UK startup Olix raises funds for AI chips", "Tech Daily",
         datetime(2026, 9, 23, 8, tzinfo=timezone.utc)),
        ("https://news.test/3", "STMicroelectronics wins car chip orders", "Chip Times",
         datetime(2026, 9, 23, 7, tzinfo=timezone.utc)),
    ]
    return n


@pytest.fixture
def web(yahoo, news):
    return FakeWeb(yahoo, news, FakeGdelt(), FakePages())


@pytest.fixture
def ctx(tmp_path, web):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        UniverseRepo(c.conn).replace_all(UNIVERSE)
        MetaRepo(c.conn).set("universe_refreshed_at", "2026-09-24T00:00:00Z")
        HealthRepo(c.conn).note_no_data("DEAD.AS", date(2026, 9, 1), dead_after=1)
        yield c


def filters(ctx, industries=(), regions=(), caps=()):
    return plan_filters(ctx, list(industries), list(regions), tuple(caps))[0]


def lines(screening):
    return [s.line for s in screening.companies]


def test_european_semiconductors_mid_cap_and_up_are_the_listed_ones_biggest_first_on_their_home_lines(ctx):
    found = screen_universe(ctx, filters(ctx, ["semiconductors"], ["Europe"], MID_AND_UP))
    assert lines(found) == CHIP_LINES, "no small cap, no US company, no OTC-only, dead or receipt line"
    assert found.matched == 6
    asml = found.companies[0]
    assert asml.evidence == "universe: Semiconductors & Semiconductor Equipment, Netherlands, Mega Cap"
    assert asml.profile.company_key == "asml"


def test_a_company_whose_universe_country_is_wrong_is_found_by_its_home_line(ctx):
    found = screen_universe(ctx, filters(ctx, ["defence"], ["Germany"]))
    assert lines(found) == ["RHM.DE"]
    assert found.companies[0].evidence == "universe: Aerospace & Defense, Germany, Large Cap"


def test_with_no_region_each_company_is_on_its_line_at_home_and_never_tesla(ctx):
    assert lines(screen_universe(ctx, filters(ctx, ["carmakers"]))) == ["7203.T", "VOW3.DE"],         "no New York line for NIO or Autobras, no receipt"
    assert lines(screen_universe(ctx, filters(ctx, ["semiconductors"], caps=["Mega Cap"]))) == ["ASML.AS", "NVDA"]
    assert lines(screen_universe(ctx, filters(ctx, ["defence"]))) == ["RHM.DE"],         "a US OTC primary: the universe's country is not trusted"


def test_regions_asked_that_name_nothing_skip_the_screen_instead_of_widening_it(ctx):
    got, notes = plan_filters(ctx, ["carmakers"], ["Atlantis"], ())
    assert not got.usable and screen_universe(ctx, got).matched == 0
    assert "no region asked for was recognised, so the universe was not screened" in notes


@pytest.mark.parametrize("words, found", [
    (["defence contractors"], ["RHM.DE"]),
    (["EV makers"], ["7203.T", "VOW3.DE"]),
])
def test_a_phrase_naming_nothing_is_tried_word_by_word(ctx, words, found):
    got, notes = plan_filters(ctx, words, [], ())
    assert lines(screen_universe(ctx, got)) == found and not notes


def test_plan_words_are_mapped_onto_the_universe_and_unknown_ones_dropped_with_a_note(ctx):
    got, notes = plan_filters(ctx, ["chipmakers", "quantum vibes"], ["the Nordics", "Atlantis"], ())
    assert [c.text for c in got.industries] == ["chipmakers"] and got.places == ("the Nordics",)
    assert got.countries == REGIONS["Nordics"]
    assert notes == ['"quantum vibes" was not recognised as a sector or industry, so the universe was not '
                     'screened for it', '"Atlantis" was not recognised as a country or region, so the universe was '
                     'not screened for it']


def test_a_screen_needs_an_industry_a_region_or_size_alone_is_too_broad(ctx):
    assert not filters(ctx, regions=["Europe"], caps=["Mid Cap"]).usable
    assert filters(ctx, ["semiconductors"]).usable
    assert not Filters().usable


# The answer: plan, news, naming, then the screen joins the shortlist.

def plan(industries=("semiconductors",), regions=("Europe",), caps=MID_AND_UP, exclude=()):
    return {"queries": ["european chipmakers"], "local": [], "market_caps": list(caps), "exclude": list(exclude),
            "industries": list(industries), "regions": list(regions)}


def model(*named, planned=None):
    m = ScriptedModel({"companies": [{"name": n, "why": w, "headlines": h} for n, w, h in named]})
    m.suggestions = planned if planned is not None else plan()
    return m


INFINEON = ("Infineon", "Opens a fab", [1])


async def test_the_trigger_question_lists_the_listed_european_chipmakers_named_first(ctx):
    view = await ask(ctx, QUESTION, model=model(INFINEON), searcher=FakeSearcher())
    got = [(line.item.symbol, line.item.universe, len(line.evidence)) for line in view.shortlist]
    assert got[0] == ("IFX.DE", "universe: Semiconductors & Semiconductor Equipment, Germany, Large Cap", 1), \
        "screened and named: first, with both kinds of evidence"
    assert [s for s, _, _ in got[1:]] == ["ASML.AS", "STM.PA", "AIXA.DE", "BESI.AS", "SOI.PA"]
    assert all(u and n == 0 for _, u, n in got[1:])
    assert view.shortlist[1].item.why == "in the universe's semiconductors in Europe; no news found names it"
    assert ("universe screen: 6 companies matched (semiconductors; Europe; mega caps, large caps, mid caps), "
            "6 shown") in view.answer.notes


async def test_the_plan_is_offered_the_universe_s_industry_names(ctx):
    m = model()
    await ask(ctx, QUESTION, model=m, searcher=FakeSearcher())
    system = m.calls[0][0][0]["content"]
    assert "Aerospace & Defense; Automobiles; Pharmaceuticals; Semiconductors & Semiconductor Equipment" in system


async def test_the_screen_shows_up_to_its_cap_and_says_how_many_matched(ctx, monkeypatch):
    monkeypatch.setattr(discovery_chat_service, "MAX_SCREENED", 2)
    view = await ask(ctx, QUESTION, model=model(), searcher=FakeSearcher())
    assert [line.item.symbol for line in view.shortlist] == ["ASML.AS", "IFX.DE"]
    assert any(n.startswith("universe screen: 6 companies matched") and n.endswith("2 shown")
               for n in view.answer.notes)


async def test_named_companies_are_price_checked_first_from_one_budget(ctx, monkeypatch):
    monkeypatch.setattr(discovery_chat_service, "MAX_PRICE_CHECKS", 1)
    monkeypatch.setattr(discovery_chat_service, "MAX_SCREEN_CHECKS", 2)
    view = await ask(ctx, QUESTION, model=model(INFINEON, ("STMicroelectronics", "Chips", [3])),
                     searcher=FakeSearcher())
    assert [line.item.symbol for line in view.shortlist] == ["IFX.DE", "STM.PA", "ASML.AS"]
    assert view.skipped == ()


async def test_the_screen_stops_at_the_deadline(ctx):
    q = discovery_chat_service.new_question(ctx, QUESTION)
    view = await discovery_chat_service.answer(ctx, q.id, model(INFINEON), FakeSearcher(), deadline=NOW)
    assert [line.item.symbol for line in view.shortlist] == ["IFX.DE"]
    assert any("the time ran out" in n for n in view.answer.notes)


async def test_a_screened_company_without_recent_prices_is_passed_over_and_counted(ctx, yahoo):
    del yahoo.series["BESI.AS"]
    view = await ask(ctx, QUESTION, model=model(), searcher=FakeSearcher())
    assert "BESI.AS" not in [line.item.symbol for line in view.shortlist]
    assert "BE Semiconductor Industries N.V." not in [i.name for i in view.skipped]
    assert any("5 shown, 1 passed over without recent prices" in n for n in view.answer.notes)


async def test_an_exclude_applies_to_screened_companies(ctx):
    planned = plan(industries=("semiconductors", "carmakers"), regions=("Germany",), exclude=("automobiles",))
    view = await ask(ctx, QUESTION, model=model(planned=planned), searcher=FakeSearcher())
    assert [line.item.symbol for line in view.shortlist] == ["IFX.DE", "AIXA.DE"]


async def test_with_no_news_found_the_screen_still_answers(ctx, news):
    news.results.clear()
    view = await ask(ctx, QUESTION, model=model(), searcher=FakeSearcher())
    assert [line.item.symbol for line in view.shortlist] == CHIP_LINES
    assert "the searches found nothing to read" in view.answer.notes


async def test_a_thematic_question_with_no_industry_is_answered_from_the_news_alone(ctx):
    view = await ask(ctx, "who supplies Infineon?", model=model(INFINEON, planned=plan(industries=(), regions=())),
                     searcher=FakeSearcher())
    assert [(line.item.symbol, line.item.universe) for line in view.shortlist] == [("IFX.DE", None)]
    assert not any(n.startswith("universe screen") for n in view.answer.notes)


async def test_the_plan_s_industry_words_count_as_the_question_s_for_a_name_matched_by_its_start(ctx):
    view = await ask(ctx, "who wins from the AI boom in Europe?", searcher=FakeSearcher(),
                     model=model(("Olix", "Raises funds", [2]), planned=plan(regions=(), caps=())))
    assert {i.name: i.skipped for i in view.skipped}["Olix"].startswith("a different business")


async def test_auto_never_adds_a_company_on_a_universe_match_alone(ctx):
    first = await ask(ctx, QUESTION, model=model(INFINEON), searcher=FakeSearcher())
    set_auto(ctx, first.answer.question_id, True)
    view = await discovery_chat_service.answer(ctx, first.answer.question_id, model(INFINEON), FakeSearcher())
    by_symbol = {line.item.symbol: line.item.validation for line in view.shortlist}
    assert by_symbol["ASML.AS"].note == ("not validated: a universe match alone is not evidence; "
                                         "auto adds need news naming it")
    assert not by_symbol["ASML.AS"].checks and not by_symbol["ASML.AS"].added
    assert by_symbol["IFX.DE"].checks, "named in the news: validated as before"


async def test_a_named_company_on_its_home_line_passes_the_line_check(ctx):
    first = await ask(ctx, QUESTION, model=model(("STMicroelectronics", "Chips", [3])), searcher=FakeSearcher())
    set_auto(ctx, first.answer.question_id, True)
    view = await discovery_chat_service.answer(ctx, first.answer.question_id,
                                               model(("STMicroelectronics", "Chips", [3])), FakeSearcher())
    stm = next(line.item for line in view.shortlist if line.item.company_key == "stm")
    assert stm.symbol == "STM.PA"
    assert next(c for c in stm.validation.checks if c.name == "line").passed
