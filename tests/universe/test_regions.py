"""Regions, countries and the words for them; a company's line on its home market."""

import pytest

from oracle.universe.listings import Listing
from oracle.universe.regions import REGIONS, countries_of, home_country, home_line, place_of


@pytest.mark.parametrize("text, holds, lacks", [
    ("Europe", {"Germany", "United Kingdom", "Switzerland", "Netherlands"}, {"United States"}),
    ("european", {"France"}, {"Japan"}),
    ("the Nordics", {"Sweden", "Norway", "Denmark", "Finland"}, {"Germany"}),
    ("Germany", {"Germany"}, {"France"}),
    ("German", {"Germany"}, {"Austria"}),
    ("UK", {"United Kingdom"}, {"Ireland"}),
    ("USA", {"United States"}, {"Canada"}),
    ("North America", {"United States", "Canada"}, {"Mexico"}),
    ("Asia", {"Japan", "South Korea", "Taiwan"}, {"Germany"}),
    ("U.S.", {"United States"}, {"Canada"}),
    ("United States of America", {"United States"}, {"Canada"}),
    ("EU", {"Germany", "France", "Ireland"}, {"United Kingdom", "Switzerland", "Norway"}),
    ("Eurozone", {"Germany", "Finland"}, {"Sweden", "United Kingdom"}),
    ("Western Europe", {"France", "United Kingdom", "Netherlands"}, {"Poland", "Hungary"}),
    ("Southeast Asia", {"Singapore", "Vietnam", "Indonesia"}, {"Japan", "China"}),
    ("Asia-Pacific", {"Japan", "Australia", "Singapore"}, {"United States"}),
    ("APAC", {"Japan", "Australia"}, {"Germany"}),
    ("South Korean", {"South Korea"}, {"Japan"}),
    ("Singaporean", {"Singapore"}, {"Malaysia"}),
    ("Mexican", {"Mexico"}, {"Brazil"}),
    ("England", {"United Kingdom"}, {"Ireland"}),
    ("Scotland", {"United Kingdom"}, {"Ireland"}),
    ("Africa", {"South Africa", "Egypt"}, {"Spain"}),
    ("emerging markets", {"India", "Brazil", "China"}, {"United States", "Germany", "Japan"}),
])
def test_a_region_country_or_nationality_names_its_countries(text, holds, lacks):
    countries = countries_of(text)
    assert holds <= countries and not lacks & countries


@pytest.mark.parametrize("text", ["Atlantis", "", "emerging tech", "grid"])
def test_a_word_naming_no_place_is_none(text):
    assert countries_of(text) is None


def test_a_nationality_word_gives_the_place_news_means():
    assert place_of("dutch") == "Netherlands" and place_of("europe's") == "Europe" and place_of("uk-based") == \
        "United Kingdom"
    assert place_of("uk") is None, "bare UK is left out: 'wins UK subsidy'"


def test_every_country_with_a_market_in_a_region_has_a_currency():
    from oracle.universe.regions import CURRENCY
    assert {"Colombia", "Peru", "Philippines", "Vietnam", "United Arab Emirates"} <= set(CURRENCY)


def test_europe_is_one_table():
    assert "United Kingdom" in REGIONS["Europe"] and "Switzerland" in REGIONS["Europe"]


def line(symbol, exchange, currency, delisted=False):
    return Listing(symbol, "X", "equity", "Netherlands", exchange, currency=currency, delisted=delisted)


@pytest.mark.parametrize("listing, country", [
    (line("ASML.AS", "AMS", "EUR"), "Netherlands"),
    (line("ASML", "NMS", "USD"), "United States"),
    (line("HSBA.L", "LSE", "GBp"), "United Kingdom"),
    (line("ASMLF", "PNK", "USD"), None),
    (line("ASME.BE", "BER", "EUR"), None),
    (line("X.L", "LSE", "USD"), None),
    (line("OLD.AS", "AMS", "EUR", delisted=True), None),
    (line("ASME.DE", "GER", None), None),
])
def test_a_home_line_is_on_a_main_board_in_its_country_s_currency(listing, country):
    assert home_country(listing) == country


def test_the_home_line_is_the_best_ranked_one_in_the_countries_asked():
    stm = [line("STM", "NYQ", "USD"), line("STM.PA", "PAR", "EUR"), line("STM.MI", "MIL", "EUR"),
           line("STMEF", "PNK", "USD")]
    assert home_line(stm, REGIONS["Europe"]).symbol == "STM.PA"
    assert home_line(stm, frozenset({"Italy"})).symbol == "STM.MI"
    assert home_line(stm, None).symbol == "STM", "anywhere: the best-ranked main-board line"
    assert home_line([line("PETR34.SA", "SAO", "BRL")], None) is not None, "receipts are the caller's to drop"
    assert home_line(stm, REGIONS["Europe"], dead={"STM.PA"}).symbol == "STM.MI"
    assert home_line(stm, frozenset({"Japan"})) is None
    assert home_line([line("STMEF", "PNK", "USD")], None) is None
