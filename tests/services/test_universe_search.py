from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.errors import UniverseNotLoaded
from oracle.services.universe_service import refresh_universe, search_universe
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase


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


def symbols(hits):
    return [h.symbol for h in hits]


def test_a_company_appears_once_as_its_primary_listing(ctx):
    hits = search_universe(ctx, "apple")
    assert symbols(hits) == ["AAPL", "APLE"]
    assert hits[0].listings == 3


def test_all_listings_shows_every_matching_line(ctx):
    assert set(symbols(search_universe(ctx, "apple", all_listings=True))) == {
        "AAPL", "APC.DE", "AAPL.MI", "APLE"
    }


def test_a_prefix_matches(ctx):
    assert symbols(search_universe(ctx, "toyo")) == ["7203.T"]


def test_an_exact_symbol_ranks_first_and_is_the_line_shown(ctx):
    assert symbols(search_universe(ctx, "tm")) == ["TM", "X.TO"]
    assert symbols(search_universe(ctx, "apc.de"))[0] == "APC.DE"


def test_bigger_companies_rank_above_better_text_matches(ctx):
    # "Samsung C&T Corp" is the shorter, closer text match, but has no size band.
    assert symbols(search_universe(ctx, "samsung")) == ["005930.KS", "SSNLF"]


def test_delisted_listings_are_hidden_unless_asked_for(ctx):
    assert search_universe(ctx, "oldco") == []
    assert symbols(search_universe(ctx, "oldco", include_delisted=True)) == ["OLD.F"]


@pytest.mark.parametrize("query, expected", [
    ("AT&T", ["T"]),
    ("coca-cola", ["KO"]),
    ('"quotes', ["QQQ.L"]),
    ("apple*", ["AAPL", "APLE"]),
    ("NEAR(apple", []),
    (") & -", []),
])
def test_punctuation_never_reaches_fts_syntax(ctx, query, expected):
    assert symbols(search_universe(ctx, query))[: len(expected) or None] == expected


def test_limit_caps_results(ctx):
    assert len(search_universe(ctx, "apple", limit=1)) == 1


def test_searching_before_a_refresh_says_how_to_load(empty_ctx):
    with pytest.raises(UniverseNotLoaded, match="oracle universe refresh"):
        search_universe(empty_ctx, "apple")
