from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.errors import UniverseUnavailable, UnknownFilterValue
from oracle.services.universe_service import (
    facets,
    refresh_universe,
    search_universe,
    universe_status,
)
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import ETF_COLUMNS, ETFS, UNIVERSE, FakeFinanceDatabase, bz2_csv, etf


@pytest.fixture
def fdb():
    return FakeFinanceDatabase(UNIVERSE, ETFS)


@pytest.fixture
async def ctx(tmp_path, fdb):
    start = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(start), fdb.transport) as c:
        await refresh_universe(c)
        yield c


def find(ctx, text="", **kw):
    return [h.symbol for h in search_universe(ctx, text, limit=100, **kw)]


def test_status_counts_each_type(ctx):
    s = universe_status(ctx)
    assert s.by_type == {"equity": (14, 11), "etf": (4, 3)}
    assert (s.listings, s.companies) == (18, 14)


async def test_a_broken_etf_file_fails_the_whole_refresh(ctx, fdb):
    before = universe_status(ctx)
    fdb.sha = "def456"
    fdb.files["etfs.bz2"] = bz2_csv(ETFS)[:-50]
    with pytest.raises(UniverseUnavailable):
        await refresh_universe(ctx)
    assert universe_status(ctx) == before


def test_type_filter_picks_funds_or_companies(ctx):
    assert find(ctx, filters={"type": ["etf"]}) == ["CSPX.L", "IGLT.L", "VWRL.L"]
    assert "CSPX.L" not in find(ctx, filters={"type": ["equity"]})
    assert {"CSPX.L", "AAPL"} <= set(find(ctx))


def test_funds_collapse_to_one_row_on_their_best_exchange(ctx):
    hits = search_universe(ctx, "s&p 500")
    assert [(h.symbol, h.type, h.listings) for h in hits] == [("CSPX.L", "etf", 2)]


def test_fund_fields_filter_funds_and_company_fields_filter_companies(ctx):
    assert find(ctx, filters={"category_group": ["fixed income"]}) == ["IGLT.L"]
    assert find(ctx, filters={"family": ["BlackRock"]}) == ["CSPX.L", "IGLT.L"]
    assert find(ctx, filters={"sector": ["Financials"]}) == ["X.TO"]
    assert find(ctx, filters={"category": ["Global"], "type": ["equity"]}) == []


def test_fund_fields_reach_the_hit(ctx):
    [hit] = search_universe(ctx, "gilts")
    assert (hit.category_group, hit.category, hit.family) == ("Fixed Income", "Government Bonds", "BlackRock")


def test_type_facets_and_fund_facets(ctx):
    assert [(f.value, f.companies) for f in facets(ctx, "type")] == [("equity", 10), ("etf", 3)]
    assert [(f.value, f.companies) for f in facets(ctx, "family")] == [("BlackRock", 2), ("Vanguard", 1)]


def test_a_type_typo_suggests_the_real_one(ctx):
    with pytest.raises(UnknownFilterValue, match="etf"):
        search_universe(ctx, "", filters={"type": ["etfs"]})


async def test_a_symbol_in_both_files_is_kept_once_as_the_equity(ctx, fdb):
    # FinanceDatabase lists CHAD in both.
    fdb.files["etfs.bz2"] = bz2_csv(ETFS + [etf("AAPL", "Apple Tracker ETF")], ETF_COLUMNS)
    await refresh_universe(ctx)
    [hit] = search_universe(ctx, "aapl")
    assert (hit.symbol, hit.type, hit.name) == ("AAPL", "equity", "Apple Inc.")
