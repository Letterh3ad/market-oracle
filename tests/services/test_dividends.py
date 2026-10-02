"""Dividends kept with prices, and the total-return series every held line reads."""

from datetime import date, datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.dividends_service import total_return_closes
from oracle.services.fx_service import update_fx
from oracle.services.prices_service import update_listing
from oracle.services.universe_service import refresh_universe
from oracle.store.bars import BarRepo
from oracle.store.dividends import DividendRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("AAPL", "USD", "America/New_York",
          [bar("2026-09-01", 100.0), bar("2026-09-14", 100.0), bar("2026-09-15", 99.0), bar("2026-09-16", 99.0),
           bar("2026-09-21", 110.0)])
    y.dividends["AAPL"] = [(date(2026, 9, 15), 1.0)]
    y.add("VOD.L", "GBp", "Europe/London", [bar("2026-09-14", 70.0), bar("2026-09-21", 72.0)])
    y.dividends["VOD.L"] = [(date(2026, 9, 21), 2.0)]
    y.add("GBPUSD=X", "USD", "Europe/London", [bar("2026-09-14", 1.25), bar("2026-09-21", 1.25)])
    return y


@pytest.fixture
async def ctx(tmp_path, yahoo):
    universe = [equity("AAPL", "Apple Inc.", currency="USD"),
                equity("VOD.L", "Vodafone", "United Kingdom", "LSE", currency="GBP")]
    web = FakeWeb(FakeFinanceDatabase(universe), yahoo)
    manager = open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport)
    c = manager.__enter__()
    await refresh_universe(c)
    yield c
    manager.__exit__(None, None, None)


async def test_a_first_fetch_keeps_the_windows_dividends_in_the_major_unit(ctx):
    await update_listing(ctx, "AAPL")
    await update_listing(ctx, "VOD.L")
    repo = DividendRepo(ctx.conn)
    assert [(d.ex_date, d.amount, d.currency) for d in repo.history("AAPL")] == [(date(2026, 9, 15), 1.0, "USD")]
    assert [(d.amount, d.currency) for d in repo.history("VOD.L")] == [(0.02, "GBP")], "pence stored as pounds"
    assert repo.span("AAPL")[1] == date(2026, 9, 21)
    assert repo.span("AAPL")[0] <= date(2026, 9, 1)


async def test_a_listing_priced_before_dividends_were_kept_is_asked_once_from_its_first_bar(ctx, yahoo):
    await update_listing(ctx, "AAPL")
    ctx.conn.execute("DELETE FROM dividends")
    ctx.conn.execute("DELETE FROM dividend_spans")
    yahoo.charts.clear()
    await update_listing(ctx, "AAPL")
    monthly = [p for s, p in yahoo.charts if p.get("interval") == "1mo"]
    assert len(monthly) == 1 and monthly[0]["events"] == "div"
    assert DividendRepo(ctx.conn).span("AAPL") == (date(2026, 9, 1), date(2026, 9, 21))
    assert [d.ex_date for d in DividendRepo(ctx.conn).history("AAPL")] == [date(2026, 9, 15)]
    yahoo.charts.clear()
    await update_listing(ctx, "AAPL")
    assert [p.get("interval") for _, p in yahoo.charts] == ["1d"], "covered now: no second ask"


async def test_the_total_return_series_reinvests_and_says_it_is_covered(ctx):
    await update_listing(ctx, "AAPL")
    await update_fx(ctx)
    local = total_return_closes(ctx, "AAPL", local=True)
    assert local.covered and local.dividends == 1
    values = dict(local.closes)
    assert values[date(2026, 9, 15)] / values[date(2026, 9, 14)] == pytest.approx(1.0), "99 + 1 against 100"
    assert values[date(2026, 9, 21)] / values[date(2026, 9, 14)] == pytest.approx(110 / 99)
    base = total_return_closes(ctx, "AAPL")
    assert dict(base.closes)[date(2026, 9, 21)] == pytest.approx(values[date(2026, 9, 21)] / 1.25)
    assert total_return_closes(ctx, "AAPL", end=date(2026, 9, 15)).closes[-1][0] == date(2026, 9, 15)


async def test_without_dividends_asked_the_series_is_price_only_and_says_so(ctx):
    await update_listing(ctx, "AAPL")
    ctx.conn.execute("DELETE FROM dividend_spans")
    tr = total_return_closes(ctx, "AAPL", local=True)
    assert not tr.covered and tr.dividends == 0
    assert dict(tr.closes)[date(2026, 9, 15)] == 99.0


def test_a_symbol_with_no_bars_has_an_empty_series(ctx):
    tr = total_return_closes(ctx, "NOPE")
    assert tr.closes == [] and not tr.covered



async def test_a_refetch_is_the_truth_for_its_window_so_a_moved_dividend_counts_once(ctx, yahoo):
    await update_listing(ctx, "AAPL")
    yahoo.dividends["AAPL"] = [(date(2026, 9, 16), 1.0)]
    yahoo.series["AAPL"].bars.append(bar("2026-09-22", 111.0))
    await update_listing(ctx, "AAPL")
    assert [d.ex_date for d in DividendRepo(ctx.conn).history("AAPL")] == [date(2026, 9, 16)]
    yahoo.dividends["AAPL"] = []
    await update_listing(ctx, "AAPL")
    assert DividendRepo(ctx.conn).history("AAPL") == [], "a cancelled one is dropped"


async def test_bars_from_another_source_ask_yahoo_only_for_the_days_not_yet_asked(ctx, yahoo):
    await update_listing(ctx, "AAPL")
    DividendRepo(ctx.conn).cover("AAPL", date(2026, 9, 1), date(2026, 9, 18), replace=True)
    yahoo.charts.clear()
    from oracle.services.http import open_client
    from oracle.services.prices_service import _store_dividends
    from oracle.market.sources import Instrument
    async with open_client(ctx) as client:
        await _store_dividends(ctx, client, Instrument("AAPL", "USD"), None, None, NOW)
    [(_, params)] = yahoo.charts
    assert params["interval"] == "1mo" and int(params["period1"]) >= int(datetime(2026, 9, 18, tzinfo=timezone.utc).timestamp())
    assert DividendRepo(ctx.conn).span("AAPL") == (date(2026, 9, 1), NOW.date())
