"""Which company a name from the news means: never a guess between several."""

from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.discovery_service import discover
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar
from tests.fakes.llm import ScriptedModel
from tests.services.test_discovery import TOYOTA, gdelt, headlines, news, reasons, settings  # noqa: F401

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
UNIVERSE = [
    equity("7203.T", "Toyota Motor Corporation", "Japan", "JPX", currency="JPY"),
    equity("USFD", "US Foods Holding Corp.", currency="USD", market_cap="Large Cap"),
    equity("USB", "U.S. Bancorp", currency="USD", market_cap="Large Cap"),
    equity("AXP", "American Express Company", currency="USD", market_cap="Mega Cap"),
    equity("AAL", "American Airlines Group Inc.", currency="USD", market_cap="Mid Cap"),
    # One band below AXP: too close to call, so "American" stays ambiguous despite AAL
    # being much further off.
    equity("AWK", "American Water Works Company, Inc.", currency="USD", market_cap="Large Cap"),
    equity("META", "Meta Platforms, Inc.", currency="USD", market_cap="Mega Cap"),
    equity("MFGX", "Meta Financial Group Inc.", currency="USD", market_cap="Small Cap"),
    equity("TGT", "Target Corporation", currency="USD", market_cap="Large Cap"),
    equity("THRL.L", "Target Healthcare REIT PLC", "United Kingdom", "LSE", currency="GBP", market_cap="Small Cap"),
    equity("V", "Visa Inc.", currency="USD", market_cap="Mega Cap"),
    equity("VSL.NS", "Visa Steel Limited", "India", "NSI", currency="INR", market_cap="Small Cap"),
    equity("SONY", "Sony Group Corporation", "Japan", "NYQ", currency="USD", market_cap="Mega Cap"),
    equity("SNEJF", "Sony Financial Corp", "Japan", "PNK", currency="USD", market_cap="Micro Cap"),
    # Symbols only, so "On"/"Open" resolve via a name's leading words, not the symbol.
    equity("ON", "Semiconductor Components Industries LLC", currency="USD", market_cap="Large Cap"),
    equity("OPEN", "Unlisted Domains Inc", currency="USD", market_cap="Small Cap"),
]


@pytest.fixture
async def ctx(tmp_path, news, gdelt):
    yahoo = FakeYahoo()
    for row in UNIVERSE:
        yahoo.add(row["symbol"], "USD", "America/New_York", [bar("2026-09-22", 10.0), bar("2026-09-23", 11.0)])
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo, news, gdelt)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        watch(c, "7203.T")
        yield c


async def named(ctx, news, *names):
    """A run where the model names each of `names`, each in a headline of its own."""
    settings(ctx, per_tick=len(names), search_themes=False, search_movers=False)
    headlines(news, TOYOTA, *(f"{n} and Toyota in talks" for n in names))
    model = ScriptedModel({"companies": [{"name": n, "relation": "partner", "headlines": [i + 1]}
                                         for i, n in enumerate(names)]})
    return await discover(ctx, model)


AMERICAN = ("ambiguous name: American Express Company, American Water Works Company, Inc., "
            "American Airlines Group Inc.")


async def test_a_name_several_companies_start_with_is_skipped_as_ambiguous_naming_them(ctx, news):
    run = await named(ctx, news, "American")
    assert run.added == () and reasons(run)["American"] == AMERICAN


async def test_a_short_name_is_never_guessed(ctx, news):
    run = await named(ctx, news, "US")
    assert run.added == () and "short" in reasons(run)["US"]


async def test_an_ambiguous_name_that_is_a_symbol_means_that_listing(ctx, news):
    run = await named(ctx, news, "Meta")
    assert [a.symbol for a in run.added] == ["META"]


@pytest.mark.parametrize(("name", "symbol"), [("Target", "TGT"), ("Visa", "V")])
async def test_an_exact_company_name_wins_over_longer_names(ctx, news, name, symbol):
    run = await named(ctx, news, name)
    assert [a.symbol for a in run.added] == [symbol]


async def test_a_shared_leading_name_resolves_to_the_clearly_bigger_company(ctx, news):
    run = await named(ctx, news, "Sony")
    assert [a.symbol for a in run.added] == ["SONY"]


async def test_a_close_pair_by_market_cap_stays_ambiguous(ctx, news):
    # AXP (Mega) and AWK (Large) are only one band apart: too close to call, even though
    # AAL (Mid) is also in the mix.
    run = await named(ctx, news, "American")
    assert run.added == () and reasons(run)["American"] == AMERICAN


async def test_a_place_word_is_never_settled_by_the_model(ctx, news):
    settings(ctx, per_tick=1, search_themes=False, search_movers=False)
    headlines(news, TOYOTA, "American and Toyota in talks")
    model = ScriptedModel({"companies": [{"name": "American", "relation": "partner", "headlines": [1]}]})
    model.roles["name_check"] = {"company": 1}
    run = await discover(ctx, model)
    assert run.added == () and reasons(run)["American"] == AMERICAN


@pytest.mark.parametrize("symbol", ["ON", "OPEN"])
async def test_a_symbol_is_only_matched_exactly_as_written(ctx, news, symbol):
    run = await named(ctx, news, symbol)
    assert [a.symbol for a in run.added] == [symbol]


@pytest.mark.parametrize("written", ["On", "on", "Open", "open"])
async def test_a_symbol_written_any_other_case_is_never_guessed(ctx, news, written):
    run = await named(ctx, news, written)
    assert run.added == ()
