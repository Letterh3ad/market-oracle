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
    equity("META", "Meta Platforms, Inc.", currency="USD", market_cap="Mega Cap"),
    equity("MFGX", "Meta Financial Group Inc.", currency="USD", market_cap="Small Cap"),
    equity("TGT", "Target Corporation", currency="USD", market_cap="Large Cap"),
    equity("THRL.L", "Target Healthcare REIT PLC", "United Kingdom", "LSE", currency="GBP", market_cap="Small Cap"),
    equity("V", "Visa Inc.", currency="USD", market_cap="Mega Cap"),
    equity("VSL.NS", "Visa Steel Limited", "India", "NSI", currency="INR", market_cap="Small Cap"),
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


async def test_a_name_several_companies_start_with_is_skipped_as_ambiguous(ctx, news):
    run = await named(ctx, news, "American")
    assert run.added == () and reasons(run)["American"] == "ambiguous name"


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
