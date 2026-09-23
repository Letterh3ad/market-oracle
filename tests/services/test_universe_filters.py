from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.errors import UniverseNotLoaded, UnknownFilterValue
from oracle.services.universe_service import facets, refresh_universe, search_universe
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase

# Companies in UNIVERSE, by market cap band then name (the no-query default order):
#   Mega:  Apple Inc. (AAPL), Samsung Electronics (005930.KS), The Coca-Cola Company (KO)
#   Large: AT&T Inc. (T), Toyota Motor Corporation (7203.T)
#   Mid:   TMX Group Limited (X.TO)
#   Small: Apple Hospitality REIT (APLE)
#   none:  "Quotes" Ltd (QQQ.L), NONAME.F, Samsung C&T Corp (SSNLF)
#   delisted, hidden: Oldco AG (OLD.F)
BY_SIZE = ["AAPL", "005930.KS", "KO", "T", "7203.T", "X.TO", "APLE", "QQQ.L", "NONAME.F", "SSNLF"]


@pytest.fixture
def empty_ctx(tmp_path):
    fdb = FakeFinanceDatabase(UNIVERSE)
    start = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(start), fdb.transport) as c:
        yield c


@pytest.fixture
async def ctx(empty_ctx):
    await refresh_universe(empty_ctx)
    return empty_ctx


def find(ctx, text="", **kw):
    return [h.symbol for h in search_universe(ctx, text, limit=100, **kw)]


def test_no_query_lists_companies_biggest_first(ctx):
    assert find(ctx) == BY_SIZE


def test_a_filter_narrows_the_list(ctx):
    assert find(ctx, filters={"country": ["Japan"]}) == ["7203.T"]


def test_repeating_a_filter_means_either(ctx):
    assert find(ctx, filters={"country": ["Japan", "Canada"]}) == ["7203.T", "X.TO"]


def test_different_filters_must_all_hold(ctx):
    assert find(ctx, filters={
        "country": ["United States"], "sector": ["Information Technology"]
    }) == ["AAPL"]


def test_filter_values_ignore_case(ctx):
    assert find(ctx, filters={"country": ["jApAn"]}) == ["7203.T"]


def test_company_filters_read_the_primary_listing(ctx):
    # Only AAPL carries a sector in the fixture; APC.DE is still Apple.
    assert find(ctx, filters={"sector": ["Information Technology"], "exchange": ["GER"]}) == ["APC.DE"]


def test_an_exchange_filter_shows_that_exchanges_line(ctx):
    assert find(ctx, filters={"exchange": ["ger"]}) == ["APC.DE"]


def test_market_cap_accepts_the_band_with_or_without_cap(ctx):
    assert find(ctx, filters={"market_cap": ["mega"]}) == ["AAPL", "005930.KS", "KO"]
    assert find(ctx, filters={"market_cap": ["Large Cap"]}) == ["T", "7203.T"]


def test_text_and_filters_combine(ctx):
    assert find(ctx, "apple", filters={"market_cap": ["small"]}) == ["APLE"]


def test_sort_by_name_ignores_case_and_falls_back_to_the_symbol(ctx):
    assert find(ctx, sort="name") == [
        "QQQ.L", "APLE", "AAPL", "T", "NONAME.F", "SSNLF", "005930.KS", "KO", "X.TO", "7203.T"
    ]


def test_sort_ties_break_by_name(ctx):
    assert find(ctx, sort="country", filters={"market_cap": ["mega"]}) == ["005930.KS", "AAPL", "KO"]


def test_descending_keeps_blanks_last(ctx):
    assert find(ctx, sort="sector", descending=True) == [
        "APLE", "AAPL", "005930.KS", "SSNLF", "X.TO", "KO", "7203.T", "T", "QQQ.L", "NONAME.F"
    ]
    assert find(ctx, sort="sector")[-3:] == ["APLE", "QQQ.L", "NONAME.F"]


def test_market_cap_sorts_smallest_first_and_desc_is_biggest_first_with_unknowns_last(ctx):
    assert find(ctx, sort="market_cap") == [
        "APLE", "X.TO", "T", "7203.T", "AAPL", "005930.KS", "KO", "QQQ.L", "NONAME.F", "SSNLF"
    ]
    assert find(ctx, sort="market_cap", descending=True) == BY_SIZE


def test_offset_pages_through_results(ctx):
    assert [h.symbol for h in search_universe(ctx, "", limit=3, offset=3)] == BY_SIZE[3:6]


@pytest.mark.parametrize("field, typo, suggestion", [
    ("country", "Germny", "Germany"),
    ("sector", "financial", "Financials"),
    ("market_cap", "mgea", "Mega Cap"),
])
def test_an_unknown_filter_value_suggests_real_ones(ctx, field, typo, suggestion):
    with pytest.raises(UnknownFilterValue, match=suggestion):
        search_universe(ctx, "", filters={field: [typo]})


def test_an_unknown_filter_field_is_refused(ctx):
    with pytest.raises(UnknownFilterValue, match="colour"):
        search_universe(ctx, "", filters={"colour": ["red"]})


def test_facets_count_active_companies_per_value(ctx):
    assert [(f.value, f.companies) for f in facets(ctx, "country")][:3] == [
        ("United States", 4), ("South Korea", 2), ("Canada", 1)
    ]
    # Oldco AG is delisted, so Germany counts only the nameless listing.
    assert ("Germany", 1) in [(f.value, f.companies) for f in facets(ctx, "country")]


def test_exchange_facets_carry_the_market_name(ctx):
    ger = next(f for f in facets(ctx, "exchange") if f.value == "GER")
    assert (ger.companies, ger.label) == (1, "XETRA")


def test_market_cap_facets_come_in_band_order(ctx):
    assert [f.value for f in facets(ctx, "market_cap")] == [
        "Mega Cap", "Large Cap", "Mid Cap", "Small Cap"
    ]


def test_facets_before_a_refresh_say_how_to_load(empty_ctx):
    with pytest.raises(UniverseNotLoaded):
        facets(empty_ctx, "country")


def test_relevance_cannot_be_reversed(ctx):
    with pytest.raises(ValueError):
        search_universe(ctx, "apple", sort="relevance", descending=True)
