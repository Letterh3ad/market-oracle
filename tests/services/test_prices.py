from datetime import date, datetime, time, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.errors import PriceUnavailable, UnknownSymbol
from oracle.services.prices_service import price_history, update_listing, update_listings
from oracle.store.health import HealthRepo
from oracle.services.universe_service import refresh_universe
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
PRICED = [
    equity("AAPL", "Apple Inc.", exchange="NMS", currency="USD"),
    equity("BP.L", "BP p.l.c.", "United Kingdom", "LSE", currency="GBp"),
    equity("BHP.AX", "BHP Group Limited", "Australia", "ASX", currency="AUD"),
]


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("AAPL", "USD", "America/New_York", [
        bar("2026-09-16", 230.0, 229.5), bar("2026-09-17", 231.0, 230.5), bar("2026-09-18", 232.0, 231.5),
    ])
    return y


@pytest.fixture
def web(yahoo):
    return FakeWeb(FakeFinanceDatabase(PRICED), yahoo)


@pytest.fixture
async def ctx(tmp_path, web):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        yield c


def closes(ctx, symbol):
    return [(b.date.isoformat(), b.close) for b in price_history(ctx, symbol)]


async def test_a_first_update_fetches_five_years_and_stores_the_bars(ctx, web):
    result = await update_listing(ctx, "AAPL")
    assert (result.symbol, result.added, result.last_date, result.source) == (
        "AAPL", 3, date(2026, 9, 18), "yahoo"
    )
    [req] = web.requests_to("query2.finance.yahoo.com")
    assert req.url.params["range"] == "5y"
    assert closes(ctx, "AAPL") == [("2026-09-16", 230.0), ("2026-09-17", 231.0), ("2026-09-18", 232.0)]


async def test_bars_keep_adjusted_close_currency_and_source(ctx):
    await update_listing(ctx, "aapl")
    last = price_history(ctx, "AAPL")[-1]
    assert (last.adj_close, last.currency, last.source, last.volume) == (231.5, "USD", "yahoo", 1000)


async def test_a_second_update_asks_only_for_recent_bars_and_adds_new_ones(ctx, web, yahoo):
    await update_listing(ctx, "AAPL")
    yahoo.series["AAPL"].bars.append(bar("2026-09-21", 233.0))
    result = await update_listing(ctx, "AAPL")
    assert result.added == 1
    second = web.requests_to("query2.finance.yahoo.com")[-1]
    assert "range" not in second.url.params
    # A week of overlap, so a partial last bar gets rewritten.
    assert int(second.url.params["period1"]) == int(datetime(2026, 9, 11, tzinfo=timezone.utc).timestamp())
    assert len(price_history(ctx, "AAPL")) == 4


async def test_a_refetch_from_a_given_day_replaces_the_stored_window(ctx, web, yahoo):
    await update_listing(ctx, "AAPL")
    yahoo.series["AAPL"].bars[0] = bar("2026-09-16", 115.0)  # re-based, as after a 2:1 split
    await update_listing(ctx, "AAPL", since=date(2026, 9, 1))
    req = web.requests_to("query2.finance.yahoo.com")[-1]
    assert int(req.url.params["period1"]) == int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
    assert closes(ctx, "AAPL")[0] == ("2026-09-16", 115.0)


async def test_several_listings_refetched_skipping_dead_ones_and_reporting_failures(ctx, web):
    HealthRepo(ctx.conn).note_no_data("BHP.AX", date(2026, 9, 1), dead_after=1)
    out = await update_listings(ctx, ["AAPL", "BP.L", "BHP.AX"], date(2026, 9, 1))
    assert [(o.symbol, o.added) for o in out] == [("AAPL", 3), ("BP.L", 0), ("BHP.AX", 0)]
    assert out[1].error and out[2].skipped and out[2].error is None
    assert not any("BHP" in str(r.url) for r in web.requests_to("query2.finance.yahoo.com"))


async def test_a_partial_last_bar_is_overwritten_by_the_next_update(ctx, yahoo):
    await update_listing(ctx, "AAPL")
    yahoo.series["AAPL"].bars[-1] = bar("2026-09-18", 240.0)
    await update_listing(ctx, "AAPL")
    assert closes(ctx, "AAPL")[-1] == ("2026-09-18", 240.0)


async def test_pence_are_stored_as_pounds(ctx, yahoo):
    yahoo.add("BP.L", "GBp", "Europe/London", [bar("2026-09-18", 542.3, 540.0)], open_at=time(8, 0))
    await update_listing(ctx, "BP.L")
    [b] = price_history(ctx, "BP.L")
    assert (b.currency, b.close, b.adj_close) == ("GBP", pytest.approx(5.423), pytest.approx(5.40))


async def test_bars_carry_the_exchanges_local_date(ctx, yahoo):
    # ASX opens 10:00 Sydney time: 23:00 UTC the previous day in summer.
    yahoo.add("BHP.AX", "AUD", "Australia/Sydney", [bar("2026-01-16", 45.0)], open_at=time(10, 0))
    await update_listing(ctx, "BHP.AX")
    assert closes(ctx, "BHP.AX") == [("2026-01-16", 45.0)]


async def test_days_without_a_close_are_skipped(ctx, yahoo):
    yahoo.series["AAPL"].bars.insert(1, (date(2026, 9, 16), None, None, None, None, None, None))
    yahoo.series["AAPL"].bars[0] = bar("2026-09-15", 229.0)
    await update_listing(ctx, "AAPL")
    assert [d for d, _ in closes(ctx, "AAPL")] == ["2026-09-15", "2026-09-17", "2026-09-18"]


async def test_history_can_be_limited_to_recent_days(ctx):
    await update_listing(ctx, "AAPL")
    assert [b.date.isoformat() for b in price_history(ctx, "AAPL", days=2)] == ["2026-09-17", "2026-09-18"]


async def test_a_symbol_yahoo_does_not_know_is_a_clear_error_that_stores_nothing(ctx):
    with pytest.raises(PriceUnavailable, match="(?i)BP.L.*no data"):
        await update_listing(ctx, "BP.L")
    assert price_history(ctx, "BP.L") == []


async def test_a_server_error_is_a_clear_error_that_stores_nothing(ctx, yahoo):
    yahoo.fail["AAPL"] = 500
    with pytest.raises(PriceUnavailable, match="AAPL"):
        await update_listing(ctx, "AAPL")
    assert price_history(ctx, "AAPL") == []


async def test_a_symbol_outside_the_universe_is_refused_with_suggestions(ctx):
    with pytest.raises(UnknownSymbol, match="AAPL"):
        await update_listing(ctx, "APPL")
