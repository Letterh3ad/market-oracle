import os
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from oracle.config import CompareConfig, Config
from oracle.jobs import JobLock
from oracle.services.context import open_context
from oracle.services.errors import JobAlreadyRunning
from oracle.services.prices_service import update_listing, update_watched
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import show_watchlist, watch
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, bz2_csv, equity, etf
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
UNIVERSE = [
    equity("AAPL", "Apple Inc.", currency="USD"),
    equity("SAP.DE", "SAP SE", "Germany", "GER", currency="EUR"),
    equity("KO", "The Coca-Cola Company", currency="USD"),
]
BENCHMARK_ETFS = [etf("VWRL.L", "Vanguard FTSE All-World UCITS ETF", "LSE", currency="GBP")]


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("AAPL", "USD", "America/New_York", [bar("2026-09-17", 200.0), bar("2026-09-18", 210.0)])
    y.add("SAP.DE", "EUR", "Europe/Berlin", [bar("2026-09-18", 150.0)])
    y.add("KO", "USD", "America/New_York", [bar("2026-09-16", 60.0), bar("2026-09-17", 61.0)])
    y.add("GBPUSD=X", "USD", "Europe/London", [bar("2026-09-17", 1.25), bar("2026-09-18", 1.25)])
    y.add("GBPEUR=X", "EUR", "Europe/London", [bar("2026-09-18", 1.20)])
    return y


@pytest.fixture
def fdb():
    return FakeFinanceDatabase(UNIVERSE, etfs=BENCHMARK_ETFS)


@pytest.fixture
def web(fdb, yahoo):
    return FakeWeb(fdb, yahoo)


@pytest.fixture
async def ctx(tmp_path, web):
    # No benchmarks by default here: most of this file's tests are about the watchlist
    # alone, and the benchmark tests below turn one on explicitly.
    config = Config(db_path=tmp_path / "o.db", compare=CompareConfig(benchmarks=()))
    with open_context(config, None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        yield c


def chart_calls(web, symbol):
    return [r for r in web.requests_to("query2.finance.yahoo.com") if r.url.path.endswith("/" + symbol)]


async def test_every_watched_listing_updates_once_with_its_fx(ctx, web):
    watch(ctx, "AAPL")
    watch(ctx, "AAPL", "tech")
    watch(ctx, "SAP.DE", "tech")
    run = await update_watched(ctx)
    assert [(o.symbol, o.added, o.error) for o in run.listings] == [("AAPL", 2, None), ("SAP.DE", 1, None)]
    assert len(chart_calls(web, "AAPL")) == 1
    assert {f.currency for f in run.fx} == {"USD", "EUR"}
    assert run.failed == []


async def test_a_failing_listing_is_reported_and_the_rest_carry_on(ctx, yahoo):
    watch(ctx, "SAP.DE")
    watch(ctx, "AAPL")
    yahoo.fail["SAP.DE"] = 500
    run = await update_watched(ctx)
    assert [o.symbol for o in run.failed] == ["SAP.DE"]
    assert "SAP.DE" in run.failed[0].error
    assert [o.symbol for o in run.listings if o.error is None] == ["AAPL"]


async def test_a_watched_symbol_gone_from_the_universe_is_reported_not_fetched(ctx, fdb, web):
    watch(ctx, "KO")
    watch(ctx, "AAPL")
    fdb.sha = "def456"
    fdb.files["equities.bz2"] = bz2_csv([r for r in UNIVERSE if r["symbol"] != "KO"])
    await refresh_universe(ctx)
    run = await update_watched(ctx)
    assert [(o.symbol, "no listing" in o.error) for o in run.failed] == [("KO", True)]
    assert chart_calls(web, "KO") == []


async def test_nothing_watched_is_nothing_to_do(ctx):
    run = await update_watched(ctx)
    assert (run.listings, run.fx) == ((), ())


async def test_a_configured_benchmark_is_priced_alongside_the_watchlist_but_never_watched(ctx, yahoo, web):
    ctx.config = replace(ctx.config, compare=replace(ctx.config.compare, benchmarks=("VWRL.L",)))
    yahoo.add("VWRL.L", "GBP", "Europe/London", [bar("2026-09-17", 100.0), bar("2026-09-18", 101.0)])
    watch(ctx, "AAPL")
    run = await update_watched(ctx)
    assert [(o.symbol, o.added, o.error) for o in run.listings] == [("AAPL", 2, None), ("VWRL.L", 2, None)]
    assert len(chart_calls(web, "VWRL.L")) == 1
    assert "VWRL.L" not in show_watchlist_symbols(ctx)


async def test_a_benchmark_already_watched_is_not_priced_twice(ctx, yahoo, web):
    ctx.config = replace(ctx.config, compare=replace(ctx.config.compare, benchmarks=("AAPL",)))
    watch(ctx, "AAPL")
    run = await update_watched(ctx)
    assert [o.symbol for o in run.listings] == ["AAPL"]
    assert len(chart_calls(web, "AAPL")) == 1


async def test_a_benchmark_not_in_the_universe_is_skipped_not_failed(ctx):
    ctx.config = replace(ctx.config, compare=replace(ctx.config.compare, benchmarks=("NOPE.L",)))
    watch(ctx, "AAPL")
    run = await update_watched(ctx)
    assert [(o.symbol, o.added, o.error) for o in run.listings if o.symbol == "AAPL"] == [("AAPL", 2, None)]
    [nope] = [o for o in run.listings if o.symbol == "NOPE.L"]
    assert (nope.added, nope.error, nope.skipped) == (0, None, "not in the universe: benchmark skipped")
    # A missing benchmark is not a failure: it never blocks the tick or the rest of the run.
    assert run.failed == []


def show_watchlist_symbols(ctx) -> set[str]:
    return {i.symbol for i in show_watchlist(ctx)}


async def test_a_second_update_is_refused_while_one_runs(ctx):
    JobLock(ctx.conn, ctx.clock, pid_alive=lambda pid: True, pid=os.getpid() + 1).acquire("prices_update", 600)
    with pytest.raises(JobAlreadyRunning, match="prices update is already running"):
        await update_watched(ctx)


async def test_watch_show_carries_the_last_price_in_both_currencies(ctx):
    watch(ctx, "AAPL")
    watch(ctx, "SAP.DE")
    await update_watched(ctx)
    lines = {i.symbol: i for i in show_watchlist(ctx)}
    aapl = lines["AAPL"]
    assert (aapl.last_date.isoformat(), aapl.last_close, aapl.close_base) == ("2026-09-18", 210.0, pytest.approx(168.0))
    assert aapl.day_change == pytest.approx(0.05)
    assert not aapl.stale
    assert lines["SAP.DE"].day_change is None  # one bar: no previous close


async def test_a_listing_never_priced_shows_without_a_price(ctx):
    watch(ctx, "AAPL")
    [line] = show_watchlist(ctx)
    assert (line.last_close, line.close_base, line.stale) == (None, None, False)


async def test_a_price_several_trading_days_old_is_stale(ctx):
    watch(ctx, "KO")
    await update_listing(ctx, "KO")
    [line] = show_watchlist(ctx)
    # Last bar Thursday 17 September: Friday's and Monday's bars are missing by Tuesday.
    assert line.stale
