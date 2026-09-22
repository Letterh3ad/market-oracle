from datetime import date, datetime, time, timezone

import pytest

from oracle.config import Config, Keys
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.fx_service import fx_history, update_fx
from oracle.services.prices_service import price_history_in_base, update_listing
from oracle.services.universe_service import refresh_universe
from tests.fakes.clock import FakeClock
from tests.fakes.eodhd import FakeEodhd
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.fx import FakeEcb, FakeFrankfurter, FakeOpenEr, noon
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
UNIVERSE = [
    equity("AAPL", "Apple Inc.", currency="USD"),
    equity("SAP.DE", "SAP SE", "Germany", "GER", currency="EUR"),
    equity("2330.TW", "Taiwan Semiconductor", "Taiwan", "TAI", currency="TWD"),
    equity("VOD.L", "Vodafone Group", "United Kingdom", "LSE", currency="GBP"),
]


def fx_bars(*pairs):
    """(iso date, units per GBP) -> Yahoo bars for a GBPXXX=X series."""
    return [bar(d, v) for d, v in pairs]


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("AAPL", "USD", "America/New_York", [bar("2026-09-10", 220.0), bar("2026-09-17", 230.0), bar("2026-09-18", 232.0)])
    y.add("SAP.DE", "EUR", "Europe/Berlin", [bar("2026-09-18", 200.0)])
    y.add("2330.TW", "TWD", "Asia/Taipei", [bar("2026-09-18", 1000.0), bar("2026-09-22", 1010.0)], open_at=time(9, 0))
    y.add("VOD.L", "GBp", "Europe/London", [bar("2026-09-18", 70.0)], open_at=time(8, 0))
    y.add("GBPUSD=X", "USD", "Europe/London", fx_bars(("2026-09-16", 1.34), ("2026-09-17", 1.35), ("2026-09-18", 1.36)), open_at=time(0, 0))
    y.add("GBPEUR=X", "EUR", "Europe/London", fx_bars(("2026-09-18", 1.15)), open_at=time(0, 0))
    return y


@pytest.fixture
def ecb():
    e = FakeEcb()
    e.per_eur = {"USD": {"2026-09-18": 1.17}, "GBP": {"2026-09-18": 0.86}}
    return e


@pytest.fixture
def frankfurter():
    f = FakeFrankfurter()
    f.per_base = {"USD": {"2026-09-18": 1.3601}}
    return f


@pytest.fixture
def open_er():
    o = FakeOpenEr(updated=noon("2026-09-22"))
    o.rates = {"TWD": 42.0, "USD": 1.37}
    return o


@pytest.fixture
def eodhd():
    return FakeEodhd("k")


@pytest.fixture
def web(yahoo, ecb, frankfurter, open_er, eodhd):
    return FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo, ecb, frankfurter, open_er, eodhd)


async def _ctx(tmp_path, web, **config):
    ctx_manager = open_context(Config(db_path=tmp_path / "o.db", **config), None, FakeClock(NOW), web.transport)
    ctx = ctx_manager.__enter__()
    await refresh_universe(ctx)
    for symbol in ("AAPL", "SAP.DE", "2330.TW", "VOD.L"):
        await update_listing(ctx, symbol)
    return ctx_manager, ctx


@pytest.fixture
async def ctx(tmp_path, web):
    manager, c = await _ctx(tmp_path, web)
    yield c
    manager.__exit__(None, None, None)


def rates(ctx, currency):
    return [(r.date.isoformat(), r.units_per_base, r.source) for r in fx_history(ctx, currency)]


def by_currency(results):
    return {r.currency: r for r in results}


async def test_only_currencies_in_stored_prices_are_fetched(ctx, web):
    results = by_currency(await update_fx(ctx))
    assert set(results) == {"USD", "EUR", "TWD"}
    asked = {r.url.path.rsplit("/", 1)[1] for r in web.requests_to("query2.finance.yahoo.com")}
    assert {"GBPUSD=X", "GBPEUR=X", "GBPTWD=X"} <= asked
    assert not any("GBPGBP" in a for a in asked)


async def test_yahoo_rates_are_stored_with_their_source(ctx):
    await update_fx(ctx)
    assert rates(ctx, "USD") == [("2026-09-16", 1.34, "yahoo"), ("2026-09-17", 1.35, "yahoo"), ("2026-09-18", 1.36, "yahoo")]


async def test_bars_convert_at_their_days_rate(ctx):
    await update_fx(ctx)
    last = price_history_in_base(ctx, "AAPL")[-1]
    assert (last.bar.date, last.rate, last.close_base) == (date(2026, 9, 18), 1.36, pytest.approx(232.0 / 1.36))


async def test_a_bar_without_a_rate_that_day_uses_the_last_within_five_days(ctx, yahoo):
    await update_fx(ctx)
    conv = {c.bar.date: c for c in price_history_in_base(ctx, "AAPL")}
    assert conv[date(2026, 9, 17)].rate == 1.35
    # 2026-09-10 is six days before the first rate: flagged, not guessed.
    assert (conv[date(2026, 9, 10)].rate, conv[date(2026, 9, 10)].close_base) == (None, None)


async def test_base_currency_prices_need_no_rate(ctx):
    [vod] = price_history_in_base(ctx, "VOD.L")
    assert (vod.rate, vod.close_base) == (1.0, pytest.approx(0.70))


async def test_ecb_fills_in_when_yahoo_fails_via_the_euro_cross(ctx, yahoo):
    yahoo.fail["GBPUSD=X"] = 500
    result = by_currency(await update_fx(ctx))["USD"]
    assert result.source == "ecb"
    assert rates(ctx, "USD") == [("2026-09-18", pytest.approx(1.17 / 0.86), "ecb")]


async def test_the_euro_itself_comes_from_the_ecb_gbp_rate(ctx, yahoo):
    yahoo.fail["GBPEUR=X"] = 500
    await update_fx(ctx)
    assert rates(ctx, "EUR") == [("2026-09-18", pytest.approx(1 / 0.86), "ecb")]


async def test_frankfurter_fills_in_when_yahoo_and_ecb_fail(ctx, yahoo, ecb):
    yahoo.fail["GBPUSD=X"] = 500
    ecb.fail = 503
    assert by_currency(await update_fx(ctx))["USD"].source == "frankfurter"
    assert rates(ctx, "USD") == [("2026-09-18", 1.3601, "frankfurter")]


async def test_a_currency_only_open_er_api_covers_converts_today_only(ctx, yahoo, open_er):
    yahoo.fail["GBPTWD=X"] = 500
    assert by_currency(await update_fx(ctx))["TWD"].source == "open_er_api"
    conv = {c.bar.date.isoformat(): c.close_base for c in price_history_in_base(ctx, "2330.TW")}
    assert conv == {"2026-09-18": None, "2026-09-22": pytest.approx(1010.0 / 42.0)}


async def test_open_er_api_is_asked_once_per_update_for_all_currencies(ctx, yahoo, open_er):
    yahoo.fail["GBPTWD=X"] = 500
    yahoo.fail["GBPUSD=X"] = 500
    await update_fx(ctx, sources=("open_er_api",))
    assert open_er.calls == 1


async def test_eodhd_is_used_when_keyed_and_earlier_sources_fail(tmp_path, web, yahoo, eodhd, ecb, frankfurter):
    manager, ctx = await _ctx(tmp_path, web, keys=Keys({"eodhd": "k"}))
    try:
        yahoo.fail["GBPUSD=X"] = 500
        ecb.fail = frankfurter.fail = 503
        eodhd.add("GBPUSD.FOREX", [("2026-09-18", 1.3599, 1.3599, 0)])
        assert by_currency(await update_fx(ctx))["USD"].source == "eodhd"
    finally:
        manager.__exit__(None, None, None)


async def test_a_currency_no_source_has_is_reported_and_the_rest_still_update(ctx, yahoo, open_er):
    yahoo.fail["GBPTWD=X"] = 500
    open_er.rates = {}
    results = by_currency(await update_fx(ctx))
    assert results["TWD"].source is None and "open_er_api" in results["TWD"].error
    assert results["USD"].source == "yahoo"


async def test_a_second_update_fetches_only_recent_rates(ctx, web, yahoo):
    await update_fx(ctx)
    yahoo.series["GBPUSD=X"].bars.append(bar("2026-09-21", 1.37))
    result = by_currency(await update_fx(ctx))["USD"]
    assert result.added == 1
    last = [r for r in web.requests_to("query2.finance.yahoo.com") if r.url.path.endswith("GBPUSD=X")][-1]
    assert "period1" in last.url.params


async def test_an_unknown_source_name_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="ecbb"):
        await update_fx(ctx, sources=("ecbb",))


async def test_a_malformed_response_counts_as_that_source_failing(ctx, yahoo, open_er):
    yahoo.fail["GBPTWD=X"] = 500
    open_er.garbage = True
    results = by_currency(await update_fx(ctx))
    assert results["TWD"].source is None and "unreadable" in results["TWD"].error
    assert results["USD"].source == "yahoo"


async def test_history_is_fetched_once_a_history_source_recovers(ctx, yahoo, web):
    yahoo.fail["GBPTWD=X"] = 500
    await update_fx(ctx)  # open.er-api: today only
    del yahoo.fail["GBPTWD=X"]
    yahoo.add("GBPTWD=X", "TWD", "Europe/London", fx_bars(("2026-09-18", 41.0), ("2026-09-22", 42.0)), open_at=time(0, 0))
    await update_fx(ctx)
    last = [r for r in web.requests_to("query2.finance.yahoo.com") if r.url.path.endswith("GBPTWD=X")][-1]
    assert last.url.params.get("range") == "5y"
    conv = {c.bar.date.isoformat(): c.close_base for c in price_history_in_base(ctx, "2330.TW")}
    assert conv["2026-09-18"] == pytest.approx(1000.0 / 41.0)


async def test_converted_bars_name_the_rates_source_for_attribution(ctx, yahoo):
    yahoo.fail["GBPTWD=X"] = 500
    await update_fx(ctx)
    last = price_history_in_base(ctx, "2330.TW")[-1]
    assert last.rate_source == "open_er_api"
    assert price_history_in_base(ctx, "VOD.L")[-1].rate_source is None
