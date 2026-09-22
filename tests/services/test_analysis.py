from datetime import date, datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.analysis_service import analyse, signal_history
from oracle.services.context import open_context
from oracle.services.errors import NoPrices, UnknownSymbol
from oracle.services.prices_service import update_listing
from oracle.services.universe_service import refresh_universe
from tests.analysts.bars import geometric, weekdays
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
DAYS = weekdays(300, date(2025, 7, 1))


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("AAPL", "USD", "America/New_York", [bar(d.isoformat(), c) for d, c in zip(DAYS, geometric(300, 0.001))])
    return y


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
async def ctx(tmp_path, yahoo, clock):
    web = FakeWeb(FakeFinanceDatabase([equity("AAPL", "Apple Inc.", currency="USD"),
                                       equity("MSFT", "Microsoft", currency="USD")]), yahoo)
    manager = open_context(Config(db_path=tmp_path / "o.db"), None, clock, web.transport)
    c = manager.__enter__()
    await refresh_universe(c)
    await update_listing(c, "AAPL")
    yield c
    manager.__exit__(None, None, None)


async def test_analyse_stores_the_trend_signal_for_the_last_bar(ctx):
    result = analyse(ctx, "aapl")
    assert [s.analyst for s in result.signals] == ["trend"]
    stored = signal_history(ctx, "AAPL", "trend")
    assert [(s.as_of, s.direction.value) for s in stored] == [(DAYS[-1], "bullish")]


async def test_a_rerun_on_the_same_data_replaces_and_a_new_bar_adds_history(ctx, yahoo, clock):
    analyse(ctx, "AAPL")
    analyse(ctx, "AAPL")
    assert len(signal_history(ctx, "AAPL", "trend")) == 1
    next_day = weekdays(301, date(2025, 7, 1))[-1]
    yahoo.series["AAPL"].bars.append(bar(next_day.isoformat(), 200.0))
    clock.advance(86400)
    await update_listing(ctx, "AAPL")
    analyse(ctx, "AAPL")
    assert [s.as_of for s in signal_history(ctx, "AAPL", "trend")] == [DAYS[-1], next_day]


async def test_an_unknown_symbol_is_refused_with_suggestions(ctx):
    with pytest.raises(UnknownSymbol, match="AAPL"):
        analyse(ctx, "AAPLE")


async def test_a_listing_without_prices_says_how_to_get_them(ctx):
    with pytest.raises(NoPrices, match="oracle prices update MSFT"):
        analyse(ctx, "MSFT")
