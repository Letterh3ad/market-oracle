"""Universe data that hid or misled discovery (session 15): companies filed under the wrong
sector, acquired companies still screened, name trade words against the question's sectors, and
everyday theme words the universe does not use."""

from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.config import Config
from oracle.market.yahoo import Quote
from oracle.services import candidate_screen
from oracle.services.business_fit import BusinessFit, NamedIn
from oracle.services.candidate_screen import Screen
from oracle.services.context import open_context
from oracle.services.discovery_chat_service import ask
from oracle.services.industry_words import classes
from oracle.services.peers_service import choose_peers
from oracle.services.prices_service import update_listing
from oracle.services.universe_screen import industry, plan_filters, screen_universe
from oracle.services.watch_service import watch
from oracle.store.health import HealthRepo
from oracle.store.paper import PaperRepo
from oracle.store.meta import MetaRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.corrections import CORRECTIONS, corrected
from tests.fakes.clock import FakeClock
from tests.fakes.gdelt import FakeGdelt
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import ScriptedModel
from tests.fakes.web import FakeWeb
from tests.fakes.web_search import FakePages, FakeSearcher
from tests.fakes.yahoo import FakeYahoo, bar
from tests.services.test_universe_screen import company

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
RETAIL = dict(sector="Consumer Discretionary", group="Retailing", industry="Specialty Retail")
DEFENCE = dict(sector="Industrials", group="Capital Goods", industry="Aerospace & Defense")
ELECTRICAL = dict(sector="Industrials", group="Capital Goods", industry="Electrical Equipment")
MACHINERY = dict(sector="Industrials", group="Capital Goods", industry="Machinery")
POWER = dict(sector="Utilities", group="Utilities", industry="Independent Power and Renewable Electricity Producers")
DIVERSIFIED = dict(sector="Financials", group="Diversified Financials", industry="Diversified Financial Services")
BANKS = dict(sector="Financials", group="Banks", industry="Banks")
GAMES = dict(sector="Communication Services", group="Media & Entertainment", industry="Entertainment")
PHARMA = dict(sector="Health Care", group="Pharmaceuticals, Biotechnology & Life Sciences", industry="Pharmaceuticals")
RHEINMETALL = "isin:DE0007030009|equity"
SIEMENS_ENERGY = "isin:DE000ENER6Y0|equity"
SCHNEIDER = "isin:FR0000121972|equity"
ARISTA = "name:arista networks inc|United States|equity"
TAKE_TWO = "name:taketwo interactive software inc|United States|equity"

UNIVERSE = [
    # Filed as a US retailer: the universe hid it from every defence screen.
    *company(RHEINMETALL, "Rheinmetall AG", "United States", "Large Cap",
             [("RNMBF", "PNK", "USD"), ("RHM.DE", "GER", "EUR")], **RETAIL),
    *company("hensoldt", "HENSOLDT AG", "Germany", "Mid Cap", [("HAG.DE", "GER", "EUR")], **DEFENCE),
    *company(SIEMENS_ENERGY, "Siemens Energy AG", "Germany", "Large Cap", [("ENR.DE", "GER", "EUR")], **POWER),
    *company(SCHNEIDER, "Schneider Electric S.E.", "France", "Large Cap", [("SU.PA", "PAR", "EUR")], **MACHINERY),
    *company("abb", "ABB Ltd", "Switzerland", "Large Cap", [("ABBN.SW", "EBS", "CHF")], **ELECTRICAL),
    *company("nexans", "Nexans S.A.", "France", "Mid Cap", [("NEX.PA", "PAR", "EUR")], **ELECTRICAL),
    *company("prysmian", "Prysmian S.p.A.", "Italy", "Mid Cap", [("PRY.MI", "MIL", "EUR")], **ELECTRICAL),
    *company("fanuc", "Fanuc Corporation", "Japan", "Large Cap", [("6954.T", "JPX", "JPY")], **MACHINERY),
    *company("rwe", "RWE AG", "Germany", "Large Cap", [("RWE.DE", "GER", "EUR")], **POWER),
    *company("orsted", "Orsted A/S", "Denmark", "Large Cap", [("ORSTED.CO", "CPH", "DKK")], **POWER),
    *company("edpr", "EDP Renovaveis, S.A.", "Portugal", "Large Cap", [("EDPR.LS", "LIS", "EUR")], **POWER),
    *company("acciona", "Acciona Energias Renovables", "Spain", "Mid Cap", [("ANE.MC", "MCE", "EUR")], **POWER),
    *company("encavis", "Encavis AG", "Germany", "Mid Cap", [("ECV.DE", "GER", "EUR")], **POWER),
    *company(ARISTA, "Arista Networks, Inc.", "United States", "Large Cap", [("ANET", "NYQ", "USD")],
             **DIVERSIFIED),
    *company("berkshire", "Berkshire Hathaway Inc.", "United States", "Mega Cap", [("BRK-B", "NYQ", "USD")],
             **DIVERSIFIED),
    *company(TAKE_TWO, "Take-Two Interactive Software, Inc.", "United States", "Large Cap",
             [("TTWO", "NMS", "USD")], **DIVERSIFIED),
    *company("nintendo", "Nintendo Co., Ltd.", "Japan", "Mega Cap", [("7974.T", "JPX", "JPY")], **GAMES),
    *company("cisco", "Cisco Systems, Inc.", "United States", "Mega Cap", [("CSCO", "NMS", "USD")],
             sector="Information Technology", group="Technology Hardware & Equipment",
             industry="Communications Equipment"),
    *company("sap", "SAP SE", "Germany", "Mega Cap", [("SAP.DE", "GER", "EUR")], sector="Information Technology",
             group="Software & Services", industry="Software"),
    *company("jpm", "JPMorgan Chase & Co.", "United States", "Mega Cap", [("JPM", "NYQ", "USD")], **BANKS),
    # Not in the corrections table: its name alone must carry its trade.
    *company("rbkb", "Rhinebeck Bancorp, Inc.", "United States", "Micro Cap", [("RBKB", "NMS", "USD")], **RETAIL),
    *company("olix", "OliX Pharmaceuticals,Inc", "South Korea", "Micro Cap", [("226950.KQ", "KOE", "KRW")],
             **PHARMA),
    *company("ifx", "Infineon Technologies AG", "Germany", "Large Cap", [("IFX.DE", "GER", "EUR")]),
    # Acquired: Yahoo has nothing for any of their lines.
    *company("dialog", "Dialog Semiconductor Plc", "United Kingdom", "Mid Cap",
             [("DLG.DE", "GER", "EUR"), ("DLG.F", "FRA", "EUR")]),
    *company("meggitt", "Meggitt PLC", "United Kingdom", "Mid Cap", [("MGGT.L", "LSE", "GBp"),
                                                                      ("MGI.F", "FRA", "EUR")], **DEFENCE),
    # Its home line has lost its data, but a sibling still trades.
    *company("aixa", "AIXTRON SE", "Germany", "Mid Cap", [("AIXA.DE", "GER", "EUR"), ("AIXA.F", "FRA", "EUR")]),
    # A single line whose last bars are months old.
    *company("stale", "Stalechip AG", "Germany", "Mid Cap", [("STAL.DE", "GER", "EUR")]),
    # Closed for a week (Golden Week): stale, not gone.
    *company("golden", "Shanghai Holidaychip Co., Ltd.", "China", "Mid Cap", [("600999.SS", "SHH", "CNY")]),
]
GONE = {"DLG.DE", "DLG.F", "MGGT.L", "MGI.F", "AIXA.DE", "STAL.DE", "600999.SS"}
DAYS = [date(2026, 9, d) for d in (22, 23, 24)]


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    for s in {x.symbol for x in UNIVERSE} - GONE:
        y.add(s, "EUR", "Europe/Berlin", [bar(d.isoformat(), 10.0) for d in DAYS])
    y.add("STAL.DE", "EUR", "Europe/Berlin", [bar("2026-06-01", 5.0), bar("2026-06-02", 5.0)])
    y.add("600999.SS", "CNY", "Asia/Shanghai", [bar("2026-09-17", 5.0), bar("2026-09-18", 5.0)])
    return y


@pytest.fixture
def web(yahoo):
    return FakeWeb(yahoo, FakeGoogleNews(), FakeGdelt(), FakePages())


@pytest.fixture
def ctx(tmp_path, web):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        UniverseRepo(c.conn).replace_all(UNIVERSE)
        MetaRepo(c.conn).set("universe_refreshed_at", "2026-09-25T00:00:00Z")
        yield c


def screened(ctx, industries, regions=()):
    return [s.line for s in screen_universe(ctx, plan_filters(ctx, list(industries), list(regions), ())[0]).companies]


def profile(ctx, symbol):
    return UniverseRepo(ctx.conn).profile(symbol)


# 1. Corrections.

def test_every_correction_names_a_whole_classification():
    for key, c in CORRECTIONS.items():
        assert key.endswith("|equity") and c.name and c.sector and c.industry_group and c.industry, key


def test_rheinmetall_is_in_a_defence_screen_though_the_universe_files_it_as_a_retailer(ctx):
    assert screened(ctx, ["defence"], ["Germany"]) == ["RHM.DE", "HAG.DE"]
    found = screen_universe(ctx, plan_filters(ctx, ["defence"], ["Germany"], ())[0]).companies[0]
    assert found.evidence == "universe: Aerospace & Defense, Germany, Large Cap"
    assert found.profile.sector == "Industrials"
    assert "RNMBF" not in screened(ctx, ["specialty retail"]), "and out of the screen it was wrongly in"


def test_electrical_equipment_holds_the_corrected_companies_and_arista_moves_to_it(ctx):
    got = screened(ctx, ["electrical equipment"], ["Europe"])
    assert "ENR.DE" in got and "SU.PA" in got
    assert "ENR.DE" not in screened(ctx, ["renewable electricity"], ["Europe"])
    assert screened(ctx, ["communications equipment"]) == ["CSCO", "ANET"]
    assert "ANET" not in screened(ctx, ["diversified financial services"])


def test_name_matching_reads_the_corrected_sector_and_country(ctx):
    p = Screen(ctx, 0).profile("RNMBF")
    assert (p.sector, p.industry, p.country) == ("Industrials", "Aerospace & Defense", "Germany")
    assert profile(ctx, "RNMBF").sector == "Consumer Discretionary", "the stored universe is left as it is"


def test_business_fit_reads_the_corrected_sector(ctx, monkeypatch):
    fit = BusinessFit(UniverseRepo(ctx.conn))
    raw = profile(ctx, "TTWO")
    assert fit.misfit(raw, "Take", NamedIn("video game entertainment stocks")) is None
    monkeypatch.delitem(CORRECTIONS, TAKE_TWO)
    assert fit.misfit(raw, "Take", NamedIn("video game entertainment stocks")).startswith("a different business")


def test_discovery_peers_are_corrected_and_the_frozen_callers_are_not(ctx):
    assert choose_peers(ctx, "ENR.DE").basis == "industry group Utilities", "research and analysts unchanged"
    got = choose_peers(ctx, "ENR.DE", corrected=True)
    assert got.basis == "industry group Capital Goods"
    assert {"RNMBF", "SU.PA", "ABBN.SW"} <= {r.symbol for r in got.rows}
    utilities = choose_peers(ctx, "RWE.DE", corrected=True)
    assert "ENR.DE" not in {r.symbol for r in utilities.rows}


def test_corrected_leaves_other_companies_and_fields_alone(ctx):
    ifx = profile(ctx, "IFX.DE")
    assert corrected(ifx) is ifx
    schneider = corrected(profile(ctx, "SU.PA"))
    assert schneider.industry == "Electrical Equipment" and schneider.country == "France"


# 2. Acquired and dead companies.

def plan(industries, regions):
    return {"queries": ["chipmakers"], "local": [], "market_caps": [], "exclude": [],
            "industries": list(industries), "regions": list(regions)}


def model(industries=("semiconductors",), regions=("Europe",)):
    m = ScriptedModel({"companies": []})
    m.suggestions = plan(industries, regions)
    return m


def charted(web, prefix):
    return [r for r in web.requests if f"/chart/{prefix}" in r.url.path]


def no_data_yesterday(ctx, *symbols):
    for s in symbols:
        HealthRepo(ctx.conn).note_no_data(s, TODAY - timedelta(days=1), dead_after=99)


async def chipmakers(ctx):
    return await ask(ctx, "European chipmakers", model=model(), searcher=FakeSearcher())


async def test_an_acquired_company_is_buried_on_its_second_day_of_no_data_then_skipped_without_a_fetch(ctx, web):
    first = await chipmakers(ctx)
    assert "DLG.DE" not in [line.item.symbol for line in first.shortlist]
    health = HealthRepo(ctx.conn)
    assert not health.get("DLG.DE").dead, "one day of no data may be a hiccup"
    ctx.clock.advance(86400)
    await chipmakers(ctx)
    today = TODAY + timedelta(days=1)
    assert health.get("DLG.DE").since == today and health.get("DLG.F").since == today, "every line, at once"
    tried = len(charted(web, "DLG"))
    assert tried == 3, "its home line on two days, then one sibling for a second opinion"
    assert "DLG.DE" not in screened(ctx, ["semiconductors"], ["Europe"])
    await chipmakers(ctx)
    assert len(charted(web, "DLG")) == tried


async def test_meggitt_leaves_a_defence_screen(ctx):
    no_data_yesterday(ctx, "MGGT.L")
    await ask(ctx, "UK defence", model=model(("defence",), ("United Kingdom",)), searcher=FakeSearcher())
    assert HealthRepo(ctx.conn).get("MGGT.L").dead and HealthRepo(ctx.conn).get("MGI.F").dead
    assert screened(ctx, ["defence"], ["United Kingdom"]) == []


async def test_a_sibling_with_prices_keeps_the_company_alive(ctx):
    no_data_yesterday(ctx, "AIXA.DE")
    line, why = await Screen(ctx, 5).priced_line(profile(ctx, "AIXA.DE"), "AIXA.DE")
    assert line is None and why.startswith("no price data")
    assert not HealthRepo(ctx.conn).get("AIXA.DE").dead and not HealthRepo(ctx.conn).get("AIXA.F").dead


async def test_recent_stored_bars_on_any_line_keep_the_company_alive(ctx, yahoo):
    yahoo.add("DLG.F", "EUR", "Europe/Berlin", [bar(d.isoformat(), 10.0) for d in DAYS])
    await update_listing(ctx, "DLG.F")
    del yahoo.series["DLG.F"]
    no_data_yesterday(ctx, "DLG.DE")
    line, _ = await Screen(ctx, 5).priced_line(profile(ctx, "DLG.DE"), "DLG.DE")
    assert line is None and not HealthRepo(ctx.conn).get("DLG.DE").dead


async def test_a_golden_week_gap_is_skipped_for_the_answer_never_buried(ctx):
    line, why = await Screen(ctx, 5).priced_line(profile(ctx, "600999.SS"), "600999.SS")
    assert line is None and why.startswith("its prices are out of date")
    assert not HealthRepo(ctx.conn).get("600999.SS").dead


async def test_a_last_bar_over_30_days_old_on_the_only_line_buries_it(ctx):
    line, why = await Screen(ctx, 5).priced_line(profile(ctx, "STAL.DE"), "STAL.DE")
    assert line is None and why.startswith("its prices are out of date")
    assert HealthRepo(ctx.conn).get("STAL.DE").since == TODAY


async def test_a_held_company_is_never_buried(ctx, monkeypatch):
    monkeypatch.setattr(PaperRepo, "held_symbols", lambda self, account=None: ["DLG.F"])
    no_data_yesterday(ctx, "DLG.DE", "DLG.F")
    line, _ = await Screen(ctx, 5).priced_line(profile(ctx, "DLG.DE"), "DLG.DE")
    assert line is None and not HealthRepo(ctx.conn).get("DLG.DE").dead


async def test_a_watched_company_is_never_buried(ctx):
    watch(ctx, "DLG.F")
    no_data_yesterday(ctx, "DLG.DE", "DLG.F")
    line, _ = await Screen(ctx, 5).priced_line(profile(ctx, "DLG.DE"), "DLG.DE")
    assert line is None and not HealthRepo(ctx.conn).get("DLG.DE").dead


async def test_a_burial_expires_after_30_days_and_the_next_screen_checks_again(ctx, yahoo):
    health = HealthRepo(ctx.conn)
    health.mark_dead(["DLG.DE", "DLG.F"], TODAY - timedelta(days=29))
    assert "DLG.DE" not in screened(ctx, ["semiconductors"], ["Europe"])
    health.mark_dead(["DLG.DE", "DLG.F"], TODAY - timedelta(days=31))
    assert "DLG.DE" in screened(ctx, ["semiconductors"], ["Europe"])
    yahoo.add("DLG.DE", "EUR", "Europe/Berlin", [bar(d.isoformat(), 10.0) for d in DAYS])
    view = await chipmakers(ctx)
    assert "DLG.DE" in [line.item.symbol for line in view.shortlist] and not health.get("DLG.DE").dead


async def test_an_expired_burial_still_gone_is_buried_again_from_today(ctx):
    health = HealthRepo(ctx.conn)
    health.mark_dead(["DLG.DE", "DLG.F"], TODAY - timedelta(days=31))
    no_data_yesterday(ctx, "DLG.DE")
    line, _ = await Screen(ctx, 5).priced_line(profile(ctx, "DLG.DE"), "DLG.DE")
    assert line is None and health.get("DLG.DE").since == TODAY


def test_an_expired_primary_is_the_live_line_again(ctx):
    health = HealthRepo(ctx.conn)
    health.mark_dead(["DLG.DE", "DLG.F"], TODAY - timedelta(days=31))
    assert Screen(ctx, 5).live_line(profile(ctx, "DLG.DE")) == "DLG.DE"
    health.mark_dead(["DLG.DE", "DLG.F"], TODAY)
    assert Screen(ctx, 5).live_line(profile(ctx, "DLG.DE")) is None


async def test_sources_down_mark_nothing_dead(ctx, yahoo):
    yahoo.fail["DLG.DE"] = 500
    yahoo.fail["DLG.F"] = 500
    no_data_yesterday(ctx, "DLG.DE")
    line, _ = await Screen(ctx, 5).priced_line(profile(ctx, "DLG.DE"), "DLG.DE")
    assert line is None and not HealthRepo(ctx.conn).get("DLG.DE").dead


async def test_with_no_check_left_for_a_second_opinion_nothing_is_marked(ctx):
    no_data_yesterday(ctx, "DLG.DE")
    line, _ = await Screen(ctx, 1).priced_line(profile(ctx, "DLG.DE"), "DLG.DE")
    assert line is None and not HealthRepo(ctx.conn).get("DLG.DE").dead


async def test_a_line_yahoo_adds_under_a_corrected_company_reads_the_correction(ctx, monkeypatch):
    async def added(ctx, symbol, bands=None):
        return "RHM.DE", None

    monkeypatch.setattr(candidate_screen, "add_from_yahoo", added)
    found = await Screen(ctx, 5)._added("Rheinmetall", Quote("RHM.DE", "Rheinmetall AG", "Rheinmetall", "EQUITY", 1.0))
    assert found.profile.sector == "Industrials" and found.profile.industry == "Aerospace & Defense"


# 3. Name trade words by sector.

def test_rhinebeck_bancorp_passes_bank_stocks_though_filed_as_a_retailer(ctx):
    fit = BusinessFit(UniverseRepo(ctx.conn))
    assert fit.misfit(profile(ctx, "RBKB"), "Rhinebeck", NamedIn("bank stocks")) is None
    assert fit.misfit(profile(ctx, "RBKB"), "Rhinebeck", NamedIn("which banks gain from higher rates?")) is None


def test_olix_is_still_refused_in_a_chip_question(ctx):
    fit = BusinessFit(UniverseRepo(ctx.conn))
    why = fit.misfit(profile(ctx, "226950.KQ"), "Olix", NamedIn("chip stocks", ("UK startup Olix raises funds",)))
    assert why and why.startswith("a different business")


# 4. Theme words.

LIVE = {
    "sector": frozenset({"Industrials", "Utilities", "Information Technology", "Financials", "Consumer Discretionary"}),
    "industry_group": frozenset({"Capital Goods", "Utilities", "Semiconductors & Semiconductor Equipment",
                                 "Diversified Financials", "Automobiles & Components", "Software & Services"}),
    "industry": frozenset({"Machinery", "Electrical Equipment", "Aerospace & Defense",
                           "Independent Power and Renewable Electricity Producers",
                           "Semiconductors & Semiconductor Equipment", "Consumer Finance", "Automobiles",
                           "Auto Components", "Software", "IT Services", "Household Durables"}),
}


@pytest.mark.parametrize("phrase, found", [
    ("robotics", "Machinery"), ("robot makers", "Machinery"), ("batteries", "Electrical Equipment"),
    ("solar", "Independent Power and Renewable Electricity Producers"), ("defence", "Aerospace & Defense"),
    ("AI chips", "Semiconductors & Semiconductor Equipment"), ("fintech", "Consumer Finance"),
    ("EV", "Automobiles"), ("EVs", "Automobiles"), ("EV makers", "Automobiles"),
    ("housebuilders", "Household Durables"),
])
def test_theme_words_name_the_universe_s_industries(phrase, found):
    got = industry(phrase, LIVE)
    assert got is not None and got.values == frozenset({found}), phrase


def test_ai_alone_names_no_industry():
    assert classes("AI", LIVE) is None
