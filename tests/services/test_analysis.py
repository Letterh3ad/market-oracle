from datetime import date, datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.analysis_service import analyse, analyse_watched, latest_signals, signal_history
from oracle.services.context import open_context
from oracle.services.errors import NoPrices, UnknownSymbol
from oracle.services.prices_service import update_listing
from oracle.services.fx_service import update_fx
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from tests.analysts.bars import geometric, weekdays
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar, summary

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
DAYS = weekdays(300, date(2025, 7, 1))
HARDWARE = dict(currency="USD", sector="Information Technology", industry_group="Hardware")
PEERS = ["DELL", "HPQ", "HPE", "LOGI", "NTAP"]


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("AAPL", "USD", "America/New_York", [bar(d.isoformat(), c) for d, c in zip(DAYS, geometric(300, 0.001))])
    y.add("GBPUSD=X", "USD", "Europe/London", [bar(d.isoformat(), 1.25) for d in DAYS])
    y.add("DELL", "USD", "America/New_York", [bar(d.isoformat(), c) for d, c in zip(DAYS, geometric(300, 0.002))])
    y.add("NOSECT", "USD", "America/New_York", [bar(d.isoformat(), 10.0) for d in DAYS])
    y.summaries["AAPL"] = summary("USD", market_cap=3e12, trailingPE=10.0, profitMargins=0.3, revenueGrowth=0.05)
    for i, peer in enumerate(PEERS):
        y.summaries[peer] = summary("USD", market_cap=1e11, trailingPE=20.0 + i, profitMargins=0.1, revenueGrowth=0.1)
    return y


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def web(yahoo):
    universe = [equity("AAPL", "Apple Inc.", market_cap="Mega Cap", **HARDWARE),
                equity("MSFT", "Microsoft", currency="USD"), equity("NOSECT", "No Sector Co", currency="USD")]
    universe += [equity(p, f"{p} Inc.", market_cap="Large Cap", **HARDWARE) for p in PEERS]
    return FakeWeb(FakeFinanceDatabase(universe), yahoo)


@pytest.fixture
async def ctx(tmp_path, web, clock):
    manager = open_context(Config(db_path=tmp_path / "o.db"), None, clock, web.transport)
    c = manager.__enter__()
    await refresh_universe(c)
    await update_listing(c, "AAPL")
    yield c
    manager.__exit__(None, None, None)


async def test_analyse_stores_the_trend_signal_for_the_last_bar(ctx):
    result = await analyse(ctx, "aapl")
    assert "trend" in [s.analyst for s in result.signals]
    stored = signal_history(ctx, "AAPL", "trend")
    assert [(s.as_of, s.direction.value) for s in stored] == [(DAYS[-1], "bullish")]


async def test_a_rerun_on_the_same_data_replaces_and_a_new_bar_adds_history(ctx, yahoo, clock):
    await analyse(ctx, "AAPL")
    await analyse(ctx, "AAPL")
    assert len(signal_history(ctx, "AAPL", "trend")) == 1
    next_day = weekdays(301, date(2025, 7, 1))[-1]
    yahoo.series["AAPL"].bars.append(bar(next_day.isoformat(), 200.0))
    clock.advance(86400)
    await update_listing(ctx, "AAPL")
    await analyse(ctx, "AAPL")
    assert [s.as_of for s in signal_history(ctx, "AAPL", "trend")] == [DAYS[-1], next_day]


async def test_an_unknown_symbol_is_refused_with_suggestions(ctx):
    with pytest.raises(UnknownSymbol, match="AAPL"):
        await analyse(ctx, "AAPLE")


async def test_a_listing_without_prices_says_how_to_get_them(ctx):
    with pytest.raises(NoPrices, match="oracle prices update MSFT"):
        await analyse(ctx, "MSFT")


async def test_analyse_runs_every_technical_and_the_combined_line(ctx):
    result = await analyse(ctx, "AAPL")
    assert [s.analyst for s in result.signals][:5] == ["trend", "rsi", "macd", "volatility", "technicals"]
    assert signal_history(ctx, "AAPL", "technicals")[-1].as_of == DAYS[-1]


async def test_analyse_scores_value_quality_and_growth_against_peers(ctx):
    result = await analyse(ctx, "AAPL")
    by = {s.analyst: s for s in result.signals}
    # P/E 10 beats all 5 peers (20..24); margin 0.3 beats all 5; growth 0.05 beats none.
    assert (by["value"].score, by["quality"].score, by["growth"].score) == (1.0, 1.0, -1.0)
    assert "industry group Hardware" in by["value"].reason


async def test_factors_without_peers_are_skipped_with_the_reason(ctx):
    await update_listing(ctx, "NOSECT")
    result = await analyse(ctx, "NOSECT")
    skipped = {k.analyst: k.reason for k in result.skipped}
    assert set(skipped) >= {"value", "quality", "growth"} and "no sector" in skipped["value"]


async def test_risk_sizes_in_base_currency_and_compares_with_other_watched_listings(ctx):
    await update_listing(ctx, "DELL")
    await update_fx(ctx)
    watch(ctx, "AAPL")
    watch(ctx, "DELL")
    risk = {s.analyst: s for s in (await analyse(ctx, "AAPL")).signals}["risk"]
    assert "corr_DELL" in dict(risk.figures) and "£" in risk.reason


async def test_risk_without_exchange_rates_says_how_to_get_them(ctx):
    skipped = {k.analyst: k.reason for k in (await analyse(ctx, "AAPL")).skipped}
    assert "oracle fx update" in skipped["risk"]


async def test_analysing_the_watchlist_reports_each_failure_and_carries_on(ctx):
    watch(ctx, "MSFT")  # no prices stored
    watch(ctx, "AAPL")
    runs = {r.symbol: r for r in await analyse_watched(ctx)}
    assert runs["AAPL"].analysis is not None and runs["AAPL"].error is None
    assert runs["MSFT"].analysis is None and "prices update" in runs["MSFT"].error


async def test_latest_signals_read_without_requests_and_mark_stale_ones(ctx, yahoo, clock, web):
    await analyse(ctx, "AAPL")
    before = len(web.requests)
    latest = {s.signal.analyst: s for s in latest_signals(ctx, "AAPL")}
    assert len(web.requests) == before
    assert not latest["trend"].stale and not latest["value"].stale
    next_day = weekdays(301, date(2025, 7, 1))[-1]
    yahoo.series["AAPL"].bars.append(bar(next_day.isoformat(), 200.0))
    clock.advance(8 * 86400)
    await update_listing(ctx, "AAPL")
    latest = {s.signal.analyst: s for s in latest_signals(ctx, "AAPL")}
    assert latest["trend"].stale and latest["value"].stale


async def test_watch_lines_and_the_company_view_carry_the_latest_signals(ctx):
    from oracle.services.company_service import company_view
    from oracle.services.watch_service import show_watchlist

    watch(ctx, "AAPL")
    assert show_watchlist(ctx)[0].signals == ()
    await analyse(ctx, "AAPL")
    line = show_watchlist(ctx)[0]
    assert {s.signal.analyst for s in line.signals} >= {"technicals", "value"}
    view = await company_view(ctx, "AAPL", refresh=False)
    assert [s.signal.analyst for s in view.signals][0] == "technicals"
