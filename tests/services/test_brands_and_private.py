"""A brand, a short name or a subsidiary resolves to the listed company it means, a startup is
told apart from a gap in the universe, and a startup is never taken for an unrelated small
listing. Fixtures from the 2026-09-24 robotics question and 03's live probe."""

from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.research.llm import ModelError
from oracle.services.business_fit import NamedIn
from oracle.net.gate import SourceGate
from oracle.services.brands import sounds_private
from oracle.market.yahoo import SEARCH_SOURCE
from oracle.services.candidate_screen import MAX_LOOKUPS, Screen
from oracle.services.context import open_context
from oracle.services.discovery_chat_service import answer, ask, new_question
from oracle.services.discovery_service import discover, theme_query
from oracle.services.universe_service import refresh_universe, search_universe
from oracle.store.universe import UniverseRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.gdelt import FakeGdelt
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import ScriptedModel
from tests.fakes.web import FakeWeb
from tests.fakes.web_search import FakePages, FakeSearcher
from tests.fakes.yahoo import FakeYahoo, bar, profile, quote
from tests.services.test_discovery import reasons, settings
from tests.services.test_discovery_matching import QUERY, found, left_off

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
ROBOTICS = "which companies lead in AI robotics?"
ANY = "who raised money this week?"
IT = dict(sector="Information Technology", industry_group="Technology Hardware & Equipment",
          industry="Electronic Equipment, Instruments & Components")
CHIPS = dict(sector="Information Technology", industry_group="Semiconductors & Semiconductor Equipment",
             industry="Semiconductors & Semiconductor Equipment")
KIT = dict(sector="Industrials", industry_group="Capital Goods", industry="Electrical Equipment")
CARS = dict(sector="Consumer Discretionary", industry_group="Automobiles & Components", industry="Automobiles")
KR = dict(currency="KRW")
UNIVERSE = [
    equity("SIE.DE", "Siemens Aktiengesellschaft", "Germany", "GER", currency="EUR", market_cap="Large Cap",
           sector="Industrials", industry_group="Capital Goods", industry="Industrial Conglomerates"),
    equity("ENR.DE", "Siemens Energy AG", "Germany", "GER", currency="EUR", market_cap="Large Cap", sector="Utilities"),
    equity("SHL.DE", "Siemens Healthineers AG", "Germany", "GER", currency="EUR", market_cap="Large Cap",
           sector="Health Care"),
    # A regional quote the universe keeps as a company of its own, with no size.
    equity("SIE.BE", "SIEMENS AG NA O.N.", "Germany", "BER", currency="EUR", sector="Industrials"),
    equity("005930.KS", "Samsung Electronics Co., Ltd.", "South Korea", "KSC", market_cap="Mega Cap", **KR, **IT),
    equity("006400.KS", "Samsung SDI Co., Ltd.", "South Korea", "KSC", market_cap="Large Cap", **KR, **IT),
    equity("207940.KS", "Samsung Biologics Co.,Ltd.", "South Korea", "KSC", market_cap="Large Cap", **KR,
           sector="Health Care", industry_group="Pharmaceuticals, Biotechnology & Life Sciences"),
    equity("SSUN.BE", "SAMSUNG EL./25 GDRS NV PF", "South Korea", "BER", currency="EUR", market_cap="Mega Cap", **IT),
    equity("LGLG.F", "LG Electronics Inc.", "South Korea", "FRA", currency="EUR", market_cap="Large Cap", **IT),
    equity("011070.KS", "LG Innotek Co., Ltd.", "South Korea", "KSC", market_cap="Mid Cap", **KR, **IT),
    equity("373220.KS", "LG Energy Solution, Ltd.", "South Korea", "KSC", market_cap="Large Cap", **KR, **KIT),
    equity("051910.KS", "LG Chem, Ltd.", "South Korea", "KSC", market_cap="Large Cap", **KR, sector="Materials",
           industry_group="Materials", industry="Chemicals"),
    equity("108670.KS", "LG Hausys, Ltd.", "South Korea", "KSC", market_cap="Small Cap", **KR, sector="Industrials",
           industry_group="Capital Goods", industry="Building Products"),
    equity("034220.KS", "LG Display Co., Ltd.", "South Korea", "KSC", market_cap="Mid Cap", **KR, **IT),
    equity("LGRDY", "Legrand SA", "France", "PNK", currency="USD", market_cap="Large Cap", **KIT),
    equity("6506.T", "YASKAWA Electric Corporation", "Japan", "JPX", currency="JPY", market_cap="Large Cap", **KIT),
    equity("YEC.DU", "YASKAWA EL. CORP.", "Japan", "DUS", currency="EUR", sector="Industrials"),
    equity("SONY", "Sony Group Corporation", "Japan", "NYQ", currency="USD", market_cap="Large Cap", **IT),
    equity("8729.T", "Sony Financial Group Inc", "Japan", "JPX", currency="JPY", market_cap="Large Cap",
           sector="Financials"),
    equity("SNEC34.SA", "SONY GROUP  DRN", "Japan", "SAO", currency="BRL", market_cap="Large Cap", **IT),
    equity("7203.T", "Toyota Motor Corporation", "Japan", "JPX", currency="JPY", market_cap="Mega Cap", **CARS),
    equity("6201.T", "Toyota Industries Corporation", "Japan", "JPX", currency="JPY", market_cap="Large Cap", **CARS),
    equity("8015.T", "Toyota Tsusho Corp.", "Japan", "JPX", currency="JPY", market_cap="Large Cap",
           sector="Materials"),
    # Live, only Berlin quotes kept as companies of their own start with "Ericsson".
    equity("ERIC-B.ST", "Telefonaktiebolaget LM Ericsson (publ)", "Sweden", "STO", currency="SEK",
           market_cap="Large Cap", **IT),
    equity("ERCB.BE", "ERICSSON B (FRIA)", "Sweden", "BER", currency="EUR", sector="Communication Services"),
    equity("ERCG.BE", "ERICSSON A (FRIA)", "Sweden", "BER", currency="EUR", sector="Communication Services"),
    equity("E1RI34.SA", "ERICSSON LM DRN", "Sweden", "SAO", currency="BRL", market_cap="Large Cap", **IT),
    equity("ARM", "Arm Holdings plc American Depositary Shares", "United Kingdom", currency="USD",
           market_cap="Large Cap", **CHIPS),
    equity("ARMK", "Aramark", currency="USD", market_cap="Large Cap", sector="Consumer Discretionary"),
    equity("BA.L", "BAE Systems plc", "United Kingdom", "LSE", currency="GBP", market_cap="Large Cap",
           sector="Industrials", industry_group="Capital Goods", industry="Aerospace & Defense"),
    equity("AMZN", "Amazon.com, Inc.", currency="USD", market_cap="Mega Cap", sector="Consumer Discretionary"),
    equity("AMZE.L", "Leverage Shares 2x Amazon ETC A", "Ireland", "LSE", currency="USD", sector="Financials"),
    equity("GOOG", "Alphabet Inc.", currency="USD", market_cap="Mega Cap", sector="Communication Services"),
    equity("STM", "STMicroelectronics N.V.", "Switzerland", "NYQ", currency="USD", market_cap="Large Cap", **CHIPS),
    equity("CATL", "Catalyst Acquisition Corp. Class A Ordinary Share", currency="USD", market_cap="Nano Cap",
           sector="Financials"),
    equity("300750.SZ", "Contemporary Amperex Technology Co., Limited", "China", "SHZ", currency="CNY",
           market_cap="Large Cap", **KIT),
    equity("META", "Meta Platforms Inc Class A", currency="USD", market_cap="Mega Cap", sector="Communication Services"),
    equity("FBOK34.SA", "Meta, Inc.", "United States", "SAO", currency="BRL", market_cap="Mega Cap",
           sector="Communication Services"),
    equity("BESI.AS", "BE Semiconductor Industries N.V.", "Netherlands", "AMS", currency="EUR", market_cap="Mid Cap",
           **CHIPS),
    equity("BESSM.LS", "BESI 8,5%PLD", "", "LIS", currency="EUR"),
    equity("LOVABLE.NS", "Lovable Lingerie Limited", "India", "NSE", currency="INR", market_cap="Nano Cap",
           sector="Consumer Discretionary", industry_group="Consumer Durables & Apparel"),
    equity("CXX.AX", "Cradle Resources Limited", "Australia", "ASX", currency="AUD", market_cap="Nano Cap",
           sector="Materials"),
    equity("MLALE.PA", "Aleph Finance Group Plc", "United Kingdom", "PAR", currency="EUR", sector="Financials"),
    equity("CERA.NS", "Cera Sanitaryware Limited", "India", "NSE", currency="INR", market_cap="Small Cap",
           sector="Industrials", industry_group="Capital Goods", industry="Building Products"),
    # The universe files this bank under Industrials.
    equity("RHBK", "Rhinebeck Bancorp, Inc.", currency="USD", market_cap="Small Cap", sector="Industrials"),
    equity("JPM", "JPMorgan Chase & Co.", currency="USD", market_cap="Mega Cap", sector="Financials",
           industry_group="Banks", industry="Banks"),
    equity("TSLA", "Tesla, Inc.", currency="USD", market_cap="Mega Cap", **CARS),
    equity("9268.T", "Optimus Group Company Limited", "Japan", "JPX", currency="JPY", market_cap="Small Cap",
           sector="Industrials"),
    equity("IMED", "Intrinsic Medicine Inc.", currency="USD", market_cap="Nano Cap", sector="Health Care"),
]


@pytest.fixture
def news():
    return FakeGoogleNews()


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    for row in UNIVERSE:
        y.add(row["symbol"], "USD", "America/New_York", [bar("2026-09-22", 10.0), bar("2026-09-23", 11.0)])
    return y


@pytest.fixture
async def ctx(tmp_path, news, yahoo):
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo, news, FakeGdelt(), FakePages())
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        yield c


def naming(*companies, picks=None, industries=()):
    """A model naming each (name, [item numbers]); `picks` answers its name checks."""
    m = ScriptedModel({"companies": [{"name": n, "why": "named in the news", "headlines": list(h)}
                                     for n, h in companies]})
    m.suggestions = {"queries": [QUERY], "local": [], "market_caps": [], "exclude": [],
                     "industries": list(industries), "regions": []}
    if picks is not None:
        m.roles["name_check"] = picks
    return m


async def answered(ctx, news, title, name, question=ROBOTICS, picks=None, industries=()):
    found(news, title)
    return await ask(ctx, question, model=naming((name, [1]), picks=picks, industries=industries),
                     searcher=FakeSearcher())


def symbols(view):
    return [line.item.symbol for line in view.shortlist]


def whys(view):
    return {line.item.name: line.item.why for line in view.shortlist}


@pytest.mark.parametrize(("name", "title", "symbol"), [
    ("Siemens", "Siemens buys a robot software maker", "SIE.DE"),
    ("Yaskawa", "Yaskawa ships its new welding robot", "6506.T"),
    ("Sony", "Sony shows a home robot", "SONY"),
    ("Ericsson", "Ericsson brings 5G to robot factories", "ERIC-B.ST"),
    ("Arm", "Arm designs a chip for robots", "ARM"),
    ("BAE", "BAE tests a robot tank", "BA.L"),
    ("Amazon", "Amazon adds a million warehouse robots", "AMZN"),
    ("STMicro", "STMicro makes sensors for robots", "STM"),
    ("Meta", "Meta builds a robotics team", "META"),
])
async def test_a_group_s_name_resolves_to_its_home_listing(ctx, news, name, title, symbol):
    view = await answered(ctx, news, title, name)
    assert symbols(view) == [symbol], left_off(view)


async def test_samsung_in_a_chips_story_is_samsung_electronics(ctx, news):
    view = await answered(ctx, news, "Samsung ramps up HBM output", "Samsung",
                          question="which chip companies matter now?")
    assert symbols(view) == ["005930.KS"], left_off(view)


async def test_lg_in_a_batteries_story_is_lg_energy_solution(ctx, news):
    view = await answered(ctx, news, "LG wins a big order", "LG", question="which battery makers are growing?")
    assert symbols(view) == ["373220.KS"], left_off(view)


async def test_the_plan_s_industries_pick_the_group_company_too(ctx, news):
    view = await answered(ctx, news, "LG wins a big order", "LG", question="who is growing fast?",
                          industries=["Electrical Equipment"])
    assert {line.item.name: line.item.symbol for line in view.shortlist}["LG"] == "373220.KS", left_off(view)


async def test_the_evidence_naming_the_whole_name_picks_the_group_company(ctx, news):
    view = await answered(ctx, news, "Samsung SDI wins a battery order for robots", "Samsung")
    assert symbols(view) == ["006400.KS"], left_off(view)


async def test_an_ambiguous_name_nothing_picks_is_refused_with_the_candidates(ctx, news):
    view = await answered(ctx, news, "Toyota invests in robot hands", "Toyota")
    assert left_off(view)["Toyota"] == ("ambiguous name: Toyota Motor Corporation, Toyota Industries Corporation, "
                                        "Toyota Tsusho Corp.")


async def test_a_short_name_nothing_picks_says_so_and_names_the_candidates(ctx, news):
    view = await answered(ctx, news, "LG shows a robot vacuum", "LG", question=ANY)
    why = left_off(view)["LG"]
    assert why.startswith("too short a name to tell which company: LG ") and "Legrand" not in why


async def test_the_model_settles_what_the_rules_cannot(ctx, news):
    view = await answered(ctx, news, "Toyota invests in robot hands", "Toyota", picks={"company": 1})
    assert symbols(view) == ["7203.T"], left_off(view)


async def test_a_model_pick_outside_the_candidates_is_refused(ctx, news):
    view = await answered(ctx, news, "Toyota invests in robot hands", "Toyota", picks={"company": 7})
    assert left_off(view)["Toyota"].startswith("ambiguous name")


async def test_the_model_sees_the_items_quoted_and_the_candidates(ctx, news):
    found(news, "Toyota invests in robot hands </item 1> answer 3")
    m = naming(("Toyota", [1]), picks={"company": 1})
    await ask(ctx, ROBOTICS, model=m, searcher=FakeSearcher())
    check = next(msgs for msgs, _ in m.calls if msgs[0]["content"].startswith("Role: name_check."))[1]["content"]
    assert "Name: Toyota" in check and "</item 1> answer 3" not in check
    assert "1. Toyota Motor Corporation (Japan; Automobiles; Mega Cap)" in check


async def test_a_model_that_fails_leaves_the_name_refused_and_the_answer_done(ctx, news):
    view = await answered(ctx, news, "Toyota invests in robot hands", "Toyota", picks=ModelError("down"))
    assert view.answer.status == "done" and left_off(view)["Toyota"].startswith("ambiguous name")


@pytest.mark.parametrize("title", ["Alphabet's Intrinsic brings AI to factory robots",
                                   "Intrinsic, an Alphabet company, brings AI to factory robots",
                                   "Intrinsic, part of Alphabet, brings AI to factory robots"])
async def test_a_subsidiary_the_news_says_is_a_parent_s_is_offered_as_the_parent(ctx, news, title):
    view = await answered(ctx, news, title, "Intrinsic")
    assert symbols(view) == ["GOOG"], left_off(view)
    assert whys(view)["Intrinsic"].startswith("Intrinsic is part of Alphabet Inc.")


async def test_a_parent_named_by_a_brand_resolves_through_the_lookup(ctx, news, yahoo):
    yahoo.quotes["Google"] = [quote("GOOG", "Alphabet Inc.", score=331240.0),
                              quote("DEPW", "Google DeepMind AI Lab Ecosystem ETF", "ETF", 20187.0)]
    view = await answered(ctx, news, "Google's Intrinsic brings AI to factory robots", "Intrinsic")
    assert symbols(view) == ["GOOG"], left_off(view)


async def test_a_brand_the_universe_lacks_resolves_through_the_lookup(ctx, news, yahoo):
    yahoo.quotes["Google"] = [quote("GOOG", "Alphabet Inc.", score=331240.0),
                              quote("DEPW", "Google DeepMind AI Lab Ecosystem ETF", "ETF", 20187.0)]
    view = await answered(ctx, news, "Google trains robots with Gemini", "Google")
    assert symbols(view) == ["GOOG"], left_off(view)
    assert whys(view)["Google"].startswith("Google is listed as Alphabet Inc.")


async def test_no_parent_is_guessed(ctx, news):
    view = await answered(ctx, news, "Intrinsic brings AI to factory robots", "Intrinsic")
    assert symbols(view) == [] and left_off(view)["Intrinsic"].startswith("not matched to Intrinsic Medicine Inc.")


async def test_a_big_company_is_not_taken_for_a_unit_of_a_deal_s_other_side(ctx, news):
    view = await answered(ctx, news, "Alphabet's Arm deal is off", "Arm")
    assert symbols(view) == ["ARM"]


async def test_a_note_is_only_on_the_match_it_explains(ctx):
    screen = Screen(ctx, 0)
    await screen.identify("Intrinsic", named_in=NamedIn(ROBOTICS, ("Alphabet's Intrinsic brings AI",)))
    assert screen.notes["Intrinsic"] == "Intrinsic is part of Alphabet Inc."
    await screen.identify("Intrinsic", named_in=NamedIn(ROBOTICS, ("Intrinsic brings AI",)))
    assert "Intrinsic" not in screen.notes


async def test_a_rival_is_not_a_parent(ctx, news):
    view = await answered(ctx, news, "Alphabet's rival Intrinsic brings AI to factory robots", "Intrinsic")
    assert symbols(view) == []


async def test_a_parent_on_the_never_list_is_refused(ctx, news):
    view = await answered(ctx, news, "Tesla's Optimus learns to fold shirts", "Optimus")
    assert symbols(view) == [] and left_off(view)["Optimus"] == "on the never list"


@pytest.mark.parametrize("name", ["Northvolt", "Flexiv", "Mistral AI"])
async def test_a_name_no_exchange_lists_is_private(ctx, news, yahoo, name):
    yahoo.quotes[name] = []
    view = await answered(ctx, news, f"{name} opens a new plant", name)
    assert left_off(view)[name] == "private, not listed"


async def test_a_startup_the_news_calls_one_is_private(ctx, news):
    view = await answered(ctx, news, "Shanghai robotics startup Flexiv raises $100M", "Flexiv")
    assert left_off(view)["Flexiv"] == "private, not listed"


async def test_a_listing_the_universe_lacks_says_so(ctx, news, yahoo):
    yahoo.quotes["Rainbow Robotics"] = [quote("277810.KQ", "Rainbow Robotics Co., Ltd.")]
    view = await answered(ctx, news, "Rainbow Robotics ships humanoids", "Rainbow Robotics")
    assert left_off(view)["Rainbow Robotics"] == "listed as Rainbow Robotics Co., Ltd. (277810.KQ), not in the universe"


async def test_a_ticker_like_brand_is_not_an_unrelated_tiny_listing(ctx, news, yahoo):
    yahoo.quotes["CATL"] = [quote("CATL", "Catalyst Acquisition Corp. Class A Ordinary Share", score=1.0e7,
                                  short="Catalyst Acquisition Corp."),
                            quote("3750.HK", "Contemporary Amperex Technology Co., Limited", short="CATL"),
                            quote("300750.SZ", "Contemporary Amperex Technology Co., Limited", short="CATL")]
    view = await answered(ctx, news, "CATL supplies batteries for humanoid robots", "CATL")
    assert symbols(view) == ["300750.SZ"], left_off(view)
    assert whys(view)["CATL"].startswith("CATL is listed as Contemporary Amperex Technology Co., Limited")


async def test_without_a_lookup_a_ticker_like_brand_is_refused(ctx, news):
    view = await answered(ctx, news, "CATL supplies batteries for humanoid robots", "CATL")
    assert symbols(view) == [] and "Catalyst Acquisition" in left_off(view)["CATL"]


async def test_a_bond_line_is_never_the_company(ctx, news, yahoo):
    yahoo.quotes["Besi"] = [quote("BESI.AS", "BE Semiconductor Industries N.V.", score=20796.0),
                            quote("BJKAS.IS", "Besiktas Futbol Yatirimlari Sanayi ve Ticaret A.S.", score=20046.0)]
    view = await answered(ctx, news, "Besi sees chip packaging demand from robots", "Besi")
    assert symbols(view) == ["BESI.AS"], left_off(view)


@pytest.mark.parametrize(("name", "title"), [
    ("Lovable", "Lovable hits $100M in revenue in eight months"),
    ("Aleph", "Aleph raises $29M to automate planning"),
    ("Cera", "Cera wins an NHS home care contract"),
])
async def test_a_startup_is_not_an_unrelated_small_listing(ctx, news, name, title):
    view = await answered(ctx, news, title, name)
    assert symbols(view) == [] and left_off(view)[name].startswith("not matched to ")


async def test_a_startup_the_news_calls_one_is_not_a_small_listing(ctx, news):
    view = await answered(ctx, news, "Cradle raises $73M Series B for AI protein design", "Cradle")
    assert symbols(view) == [] and left_off(view)["Cradle"].startswith("private, not listed")


async def test_the_model_is_not_trusted_to_confirm_a_small_listing(ctx, news):
    # Measured on the 4B model: it said yes to Lovable, Aleph and Cera, and no to a true match.
    view = await answered(ctx, news, "Lovable hits $100M in revenue in eight months", "Lovable",
                          picks={"company": 1})
    assert symbols(view) == []


async def test_the_listing_s_whole_name_in_the_news_confirms_it(ctx, news):
    view = await answered(ctx, news, "Cera Sanitaryware opens a robot-run plant", "Cera")
    assert symbols(view) == ["CERA.NS"]


async def test_a_name_s_trade_word_counts_as_the_question_s_sector(ctx, news):
    view = await answered(ctx, news, "Rhinebeck raises its dividend", "Rhinebeck",
                          question="which bank stocks look cheap?")
    assert symbols(view) == ["RHBK"], left_off(view)


async def test_tick_discovery_resolves_brands_parents_and_private_names(ctx, news, yahoo):
    yahoo.quotes["Mistral AI"] = []
    settings(ctx, per_tick=3, themes=("robotics",), search_related=False, search_movers=False)
    titles = ["Alphabet's Intrinsic brings AI to factory robots", "Siemens buys a robot software maker",
              "Mistral AI opens a robotics lab", "Cradle raises $73M Series B"]
    news.results[theme_query("robotics")] = [
        (f"https://news.test/{i}", t, "Wire", datetime(2026, 9, 23, 8, tzinfo=timezone.utc))
        for i, t in enumerate(titles)]
    run = await discover(ctx, ScriptedModel({"companies": [
        {"name": "Intrinsic", "headlines": [1]}, {"name": "Siemens", "headlines": [2]},
        {"name": "Mistral AI", "headlines": [3]}, {"name": "Cradle", "headlines": [4]}]}))
    assert [a.symbol for a in run.added] == ["GOOG", "SIE.DE"]
    assert "Intrinsic is part of Alphabet Inc." in run.added[0].why
    assert reasons(run)["Mistral AI"] == "private, not listed"
    assert reasons(run)["Cradle"].startswith("private, not listed")


# Gate round 1.

@pytest.mark.parametrize("title", ["Amazon's Anthropic investment pays off", "Alphabet's Anthropic stake grows",
                                   "Why Amazon's Anthropic bet matters", "Amazon's Anthropic deal is done",
                                   "Alphabet's Anthropic backing pays off"])
async def test_a_stake_in_a_company_is_not_owning_it(ctx, news, title):
    view = await answered(ctx, news, title, "Anthropic")
    assert symbols(view) == [], whys(view)


@pytest.mark.parametrize("title", ["Alphabet-owned Intrinsic brings AI to factory robots",
                                   "Alphabet's robotics unit Intrinsic brings AI to factory robots"])
async def test_plain_ownership_still_names_the_parent(ctx, news, title):
    view = await answered(ctx, news, title, "Intrinsic")
    assert symbols(view) == ["GOOG"], left_off(view)


async def test_a_never_list_name_before_the_name_refuses_a_small_listing(ctx, news):
    view = await answered(ctx, news, "Tesla Optimus learns to fold shirts", "Optimus")
    assert symbols(view) == [] and left_off(view)["Optimus"] == "on the never list"


async def test_a_listed_company_s_name_before_the_name_refuses_a_small_listing(ctx, news):
    view = await answered(ctx, news, "Alphabet Intrinsic opens a robot lab", "Intrinsic")
    assert "IMED" not in symbols(view), left_off(view)


def names(*names_):
    return [(n, [1]) for n in names_]


async def test_yahoo_is_searched_a_few_times_per_answer_at_most(ctx, news, yahoo):
    unknown = ["Figure", "Apptronik", "Agility", "Unitree", "Sanctuary", "Physical Intelligence", "Skild"]
    found(news, " and ".join(unknown) + " raise money")
    await ask(ctx, ROBOTICS, model=naming(*names(*unknown)), searcher=FakeSearcher())
    assert len(yahoo.searched) == MAX_LOOKUPS


async def test_a_failing_search_never_pauses_prices(ctx, news, yahoo):
    yahoo.search_status = 429
    unknown = ["Figure", "Apptronik", "Agility", "Unitree", "Skild"]
    found(news, " and ".join(unknown) + " and Siemens raise money")
    view = await ask(ctx, ROBOTICS, model=naming(*names(*unknown, "Siemens")), searcher=FakeSearcher())
    assert symbols(view) == ["SIE.DE"]
    gate = SourceGate(ctx.conn, ctx.clock, ctx.config.sources)
    assert gate.status("yahoo").state != "paused" and gate.status(SEARCH_SOURCE).state == "paused"


@pytest.mark.parametrize("body", ["<html>EU consent</html>", '{"quotes": [{"symbol": "X", "score": "high"}]}',
                                  '{"quotes": "none"}', "[]"])
async def test_a_garbled_search_answer_is_no_answer(ctx, news, yahoo, body):
    yahoo.quotes["Figure"] = body
    view = await answered(ctx, news, "Figure raises money for humanoids", "Figure")
    assert left_off(view)["Figure"] == "not in the universe under that name"


async def test_past_the_deadline_names_are_matched_without_searches_or_model(ctx, news, yahoo):
    found(news, "Toyota and Figure build robots")
    m = naming(*names("Toyota", "Figure"), picks={"company": 1})
    q = new_question(ctx, ROBOTICS)
    view = await answer(ctx, q.id, m, FakeSearcher(), deadline=NOW)
    assert yahoo.searched == [] and not any(c[0][0]["content"].startswith("Role: name_check.") for c in m.calls)
    assert left_off(view)["Toyota"].startswith("ambiguous name")


async def test_the_model_is_asked_once_per_name_and_news(ctx, news):
    screen = Screen(ctx, 0, naming(picks={"company": 1}))
    named_in = NamedIn(ROBOTICS, ("Toyota invests in robot hands",))
    first = await screen.resolve("Toyota", named_in)
    await screen.resolve("toyota", named_in)
    calls = [c for c in screen._model.calls if c[0][0]["content"].startswith("Role: name_check.")]
    assert first.symbol == "7203.T" and len(calls) == 1


async def test_the_model_never_picks_from_more_than_it_is_shown(ctx, news):
    view = await answered(ctx, news, "LG shows a robot vacuum", "LG", question=ANY, picks={"company": 1})
    assert "LG" not in {line.item.name for line in view.shortlist}
    assert left_off(view)["LG"].startswith("too short a name to tell which company")


async def test_a_listing_yahoo_ranks_first_outside_the_universe_is_not_private(ctx, news, yahoo):
    yahoo.quotes["Foxconn"] = [quote("2317.TW", "Hon Hai Precision Industry Co., Ltd.", score=500000.0),
                               quote("FXCOF", "Foxconn Interconnect", score=20000.0)]
    view = await answered(ctx, news, "Foxconn builds humanoids", "Foxconn")
    assert left_off(view)["Foxconn"] == "listed as Hon Hai Precision Industry Co., Ltd. (2317.TW), not in the universe"


async def test_a_named_line_already_tracked_but_passed_over_says_so(ctx, news, yahoo):
    yahoo.quotes["LG Hausys Interior"] = [quote("108670.KS", "LG Hausys, Ltd.", score=30000.0),
                                          quote("9999.KS", "Hausys Other Co., Ltd.", score=20000.0)]
    view = await answered(ctx, news, "LG Hausys Interior builds robot kitchens", "LG Hausys Interior")
    assert left_off(view)["LG Hausys Interior"] == "listed as LG Hausys, Ltd. (108670.KS), already in the universe"
    assert "108670.KS" not in yahoo.profiled


async def test_yahoo_s_private_company_line_says_private(ctx, news, yahoo):
    yahoo.quotes["Anthropic"] = [quote("ANTH.PVT", "Anthropic", kind="PRIVATE_COMPANY", score=90000.0)]
    view = await answered(ctx, news, "Anthropic ships a robot model", "Anthropic")
    assert left_off(view)["Anthropic"] == "private, not listed"


@pytest.mark.parametrize(("name", "title", "private"), [
    ("Nvidia", "Nvidia raises guidance ahead of next round of tariffs", False),
    ("Rheinmetall", "Rheinmetall raises capital in a placement round", False),
    ("Siemens", "Siemens buys a startup", False),
    ("Flexiv", "Flexiv raises $100M in a Series C round", True),
    ("Flexiv", "Shanghai robotics startup Flexiv shows an arm", True),
    ("Flexiv", "Flexiv, a Shanghai robotics start-up, shows an arm", True),
    ("Flexiv", "Flexiv raises money in a funding round", True),
])
def test_private_words_must_be_about_the_name(name, title, private):
    assert sounds_private(name, [title]) is private


async def test_samsung_in_a_robotics_story_needs_the_model(ctx, news):
    view = await answered(ctx, news, "Samsung shows a humanoid robot", "Samsung")
    assert left_off(view)["Samsung"] == ("ambiguous name: Samsung Electronics Co., Ltd., Samsung Biologics Co.,Ltd., "
                                         "Samsung SDI Co., Ltd.")
    view = await answered(ctx, news, "Samsung shows a humanoid robot", "Samsung", picks={"company": 1})
    assert symbols(view) == ["005930.KS"], left_off(view)


async def test_a_listing_the_universe_lacks_is_added_from_yahoo_and_screened(ctx, news, yahoo):
    yahoo.quotes["Rainbow Robotics"] = [quote("277810.KQ", "Rainbow Robotics Co., Ltd.")]
    yahoo.summaries["277810.KQ"] = profile("Rainbow Robotics Co., Ltd.", "KOE", "KRW", 4e12, "South Korea",
                                           "Industrials", "Specialty Industrial Machinery")
    yahoo.add("277810.KQ", "KRW", "Asia/Seoul", [bar("2026-09-22", 10.0), bar("2026-09-23", 11.0)])
    view = await answered(ctx, news, "Rainbow Robotics ships humanoids", "Rainbow Robotics")
    assert symbols(view) == ["277810.KQ"], left_off(view)
    assert "added to the universe from Yahoo" in whys(view)["Rainbow Robotics"]
    assert search_universe(ctx, "Rainbow Robotics")[0].symbol == "277810.KQ"


async def test_a_listing_yahoo_names_as_a_fund_is_not_added(ctx, news, yahoo):
    yahoo.quotes["Rainbow Robotics"] = [quote("277810.KQ", "Rainbow Robotics Co., Ltd.")]
    yahoo.summaries["277810.KQ"] = profile("Rainbow Robotics Co., Ltd.", "KOE", "KRW", None, quote_type="ETF")
    view = await answered(ctx, news, "Rainbow Robotics ships humanoids", "Rainbow Robotics")
    assert left_off(view)["Rainbow Robotics"] == "listed as Rainbow Robotics Co., Ltd. (277810.KQ), not in the universe"
    assert UniverseRepo(ctx.conn).resolve("277810.KQ") is None


async def test_adding_from_yahoo_shares_the_answer_s_lookups(ctx, news, yahoo):
    unknown = ["Figure", "Apptronik", "Agility", "Unitree", "Sanctuary", "Physical Intelligence", "Skild"]
    for i, name in enumerate(unknown):
        yahoo.quotes[name] = [quote(f"{i}0000.KQ", f"{name} Co., Ltd.")]
        yahoo.summaries[f"{i}0000.KQ"] = profile(f"{name} Co., Ltd.", "KOE", "KRW", 1e12, "South Korea")
    found(news, " and ".join(unknown) + " raise money")
    await ask(ctx, ROBOTICS, model=naming(*names(*unknown)), searcher=FakeSearcher())
    assert len(yahoo.searched) + len(yahoo.profiled) == MAX_LOOKUPS and yahoo.profiled


async def test_tesla_found_on_yahoo_is_never_added(ctx, news, yahoo):
    yahoo.quotes["Tesla Energy"] = [quote("TSLAE", "Tesla Energy Inc.")]
    yahoo.summaries["TSLAE"] = profile("Tesla Energy Inc.", "NMS", "USD", 1e11, "United States")
    view = await answered(ctx, news, "Tesla Energy ships grid batteries", "Tesla Energy")
    assert symbols(view) == [] and UniverseRepo(ctx.conn).resolve("TSLAE") is None


async def test_an_unrelated_lone_lead_on_yahoo_is_not_added_or_matched(ctx, news, yahoo):
    yahoo.quotes["Skild"] = [quote("8888.T", "Skildex Holdings", score=90000.0)]
    yahoo.summaries["8888.T"] = profile("Skildex Holdings", "JPX", "USD", 2e8, "Japan", "Industrials")
    yahoo.add("8888.T", "JPY", "Asia/Tokyo", [bar("2026-09-22", 10.0), bar("2026-09-23", 11.0)])
    view = await answered(ctx, news, "Skild raises money for robot brains", "Skild")
    assert symbols(view) == [] and UniverseRepo(ctx.conn).resolve("8888.T") is None
    assert "added" not in left_off(view)["Skild"]


async def test_a_big_lone_lead_on_yahoo_is_added(ctx, news, yahoo):
    yahoo.quotes["Hanwha Aero"] = [quote("012450.KS", "Hanwha Aerospace Co., Ltd.", score=90000.0)]
    yahoo.summaries["012450.KS"] = profile("Hanwha Aerospace Co., Ltd.", "KSC", "USD", 3e10, "South Korea",
                                           "Industrials")
    yahoo.add("012450.KS", "KRW", "Asia/Seoul", [bar("2026-09-22", 10.0), bar("2026-09-23", 11.0)])
    view = await answered(ctx, news, "Hanwha Aero builds robot tanks", "Hanwha Aero")
    assert symbols(view) == ["012450.KS"], left_off(view)
    assert "added to the universe from Yahoo" in whys(view)["Hanwha Aero"]


async def test_a_small_lead_already_in_the_universe_is_not_matched_or_claimed_added(ctx, news, yahoo):
    yahoo.quotes["Figure"] = [quote("CXX.AX", "Cradle Resources Limited", score=90000.0)]
    view = await answered(ctx, news, "Figure raises money for humanoids", "Figure")
    assert "CXX.AX" not in symbols(view)
    assert "added" not in left_off(view)["Figure"]
    assert yahoo.profiled == []


async def test_a_small_tracked_lead_does_not_block_the_named_line_below_it(ctx, news, yahoo):
    yahoo.quotes["Figure"] = [quote("CXX.AX", "Cradle Resources Limited", score=900000.0),
                              quote("4444.T", "Figure Robotics Inc.", score=100.0)]
    yahoo.summaries["4444.T"] = profile("Figure Robotics Inc.", "JPX", "USD", 5e9, "Japan", "Industrials")
    yahoo.add("4444.T", "JPY", "Asia/Tokyo", [bar("2026-09-22", 10.0), bar("2026-09-23", 11.0)])
    view = await answered(ctx, news, "Figure raises money for humanoids", "Figure")
    assert symbols(view) == ["4444.T"], left_off(view)
    assert "added to the universe from Yahoo" in whys(view)["Figure"]
