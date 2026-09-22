from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.errors import InvalidTag, UnknownFilterValue, UnknownSymbol
from oracle.services.tags_service import add_tag, list_tags, remove_tag, tags_of
from oracle.services.universe_service import refresh_universe, search_universe
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase, bz2_csv


@pytest.fixture
def fdb():
    return FakeFinanceDatabase(UNIVERSE)


@pytest.fixture
async def ctx(tmp_path, fdb):
    start = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(start), fdb.transport) as c:
        await refresh_universe(c)
        yield c


def tagged(ctx, *tags):
    return [h.symbol for h in search_universe(ctx, "", tags=list(tags), limit=100)]


def test_tagging_any_listing_tags_the_whole_company(ctx):
    add_tag(ctx, "APC.DE", "ai")
    assert tags_of(ctx, "AAPL") == ["ai"]
    hits = search_universe(ctx, "apple")
    assert [(h.symbol, h.tags) for h in hits] == [("AAPL", ("ai",)), ("APLE", ())]


def test_tags_are_trimmed_and_lower_cased(ctx):
    tagged = add_tag(ctx, "aapl", "  AI   Hardware ")
    assert (tagged.tag, tagged.symbol, tagged.name) == ("ai hardware", "AAPL", "Apple Inc.")
    assert tags_of(ctx, "AAPL") == ["ai hardware"]


def test_adding_a_tag_twice_is_a_no_op_even_through_another_listing(ctx):
    add_tag(ctx, "AAPL", "ai")
    add_tag(ctx, "AAPL", "AI")
    add_tag(ctx, "APC.DE", "ai")
    assert tags_of(ctx, "AAPL.MI") == ["ai"]
    assert [(t.tag, t.symbols) for t in list_tags(ctx)] == [("ai", ("AAPL",))]


def test_symbols_are_matched_without_case(ctx):
    add_tag(ctx, "apc.de", "ai")
    assert tags_of(ctx, "aapl") == ["ai"]


def test_removing_through_any_listing_untags_the_company(ctx):
    add_tag(ctx, "APC.DE", "ai")
    assert remove_tag(ctx, "AAPL", "AI")
    assert tags_of(ctx, "APC.DE") == []
    assert not remove_tag(ctx, "AAPL", "ai")


def test_several_tags_filter_to_companies_carrying_them_all(ctx):
    add_tag(ctx, "AAPL", "ai")
    add_tag(ctx, "AAPL", "us big")
    add_tag(ctx, "KO", "us big")
    assert tagged(ctx, "us big") == ["AAPL", "KO"]
    assert tagged(ctx, "us big", "ai") == ["AAPL"]


def test_an_unknown_tag_filter_suggests_real_ones(ctx):
    add_tag(ctx, "AAPL", "ai hardware")
    with pytest.raises(UnknownFilterValue, match="ai hardware"):
        tagged(ctx, "ai hardwre")


async def test_tags_survive_a_refresh_and_flag_vanished_companies(ctx, fdb):
    add_tag(ctx, "APC.DE", "ai")
    add_tag(ctx, "KO", "ai")
    fdb.sha = "def456"
    fdb.files["equities.bz2"] = bz2_csv([r for r in UNIVERSE if r["symbol"] != "KO"])
    await refresh_universe(ctx)
    [summary] = list_tags(ctx)
    assert (summary.tag, summary.symbols, summary.orphaned) == ("ai", ("AAPL",), ("KO",))

    fdb.sha = "ghi789"
    fdb.files["equities.bz2"] = bz2_csv(UNIVERSE)
    await refresh_universe(ctx)
    assert tagged(ctx, "ai") == ["AAPL", "KO"]


async def test_an_orphaned_tag_can_still_be_removed(ctx, fdb):
    add_tag(ctx, "KO", "ai")
    fdb.sha = "def456"
    fdb.files["equities.bz2"] = bz2_csv([r for r in UNIVERSE if r["symbol"] != "KO"])
    await refresh_universe(ctx)
    assert remove_tag(ctx, "KO", "ai")
    assert list_tags(ctx) == []


def test_an_unknown_symbol_is_refused_with_suggestions(ctx):
    with pytest.raises(UnknownSymbol, match="AAPL"):
        add_tag(ctx, "APPL", "ai")


@pytest.mark.parametrize("bad", ["", "   ", "a,b", "x" * 41])
def test_a_bad_tag_is_refused(ctx, bad):
    with pytest.raises(InvalidTag):
        add_tag(ctx, "AAPL", bad)


async def test_a_tag_survives_while_any_of_the_companys_listings_does(ctx, fdb):
    add_tag(ctx, "APC.DE", "ai")
    fdb.sha = "def456"
    fdb.files["equities.bz2"] = bz2_csv([r for r in UNIVERSE if r["symbol"] != "APC.DE"])
    await refresh_universe(ctx)
    assert tagged(ctx, "ai") == ["AAPL"]
    assert list_tags(ctx)[0].orphaned == ()


async def test_an_orphaned_symbols_tags_can_still_be_listed(ctx, fdb):
    add_tag(ctx, "KO", "drinks")
    fdb.sha = "def456"
    fdb.files["equities.bz2"] = bz2_csv([r for r in UNIVERSE if r["symbol"] != "KO"])
    await refresh_universe(ctx)
    assert tags_of(ctx, "ko") == ["drinks"]


def test_removing_from_an_unknown_symbol_suggests_real_ones(ctx):
    with pytest.raises(UnknownSymbol, match="AAPL"):
        remove_tag(ctx, "APPL", "ai")
