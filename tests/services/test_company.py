from datetime import datetime, timezone

import pytest

from oracle.config import Config, Keys
from oracle.services.company_service import company_view
from oracle.services.context import open_context
from oracle.services.fx_service import update_fx
from oracle.services.prices_service import update_listing
from oracle.services.tags_service import add_tag
from oracle.services.universe_service import refresh_universe
from tests.fakes.clock import FakeClock
from tests.fakes.eodhd import FakeEodhd
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar, summary

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
APPLE = "US0378331005"
UNIVERSE = [
    equity("AAPL", "Apple Inc.", isin=APPLE, currency="USD", sector="Information Technology",
           industry="Technology Hardware", website="https://www.apple.com"),
    equity("APC.DE", "Apple Inc.", exchange="GER", isin=APPLE, currency="EUR"),
    equity("BP.L", "BP p.l.c.", "United Kingdom", "LSE", currency="GBP", sector="Energy"),
]


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("AAPL", "USD", "America/New_York", [bar("2026-09-17", 200.0), bar("2026-09-18", 210.0)])
    y.add("GBPUSD=X", "USD", "Europe/London", [bar("2026-09-18", 1.25)])
    y.summaries["AAPL"] = summary(
        "USD", market_cap=4.0e12, trailingPE=39.0, forwardPE=35.4, priceToBook=46.2, dividendYield=0.0032,
        profitMargins=0.276, returnOnEquity=1.49, revenueGrowth=0.164, debtToEquity=78.4, beta=1.085,
    )
    y.summaries["BP.L"] = summary("GBp", market_cap=8.4e10, trailingPE=20.9)
    return y


@pytest.fixture
def eodhd():
    e = FakeEodhd("k")
    e.fundamentals["AAPL.US"] = {
        "Highlights": {"MarketCapitalization": 3.9e12, "PERatio": 38.0, "DividendYield": 0.003,
                       "ProfitMargin": 0.27, "ReturnOnEquityTTM": 1.5, "QuarterlyRevenueGrowthYOY": 0.16},
        "Valuation": {"ForwardPE": 34.0, "PriceBookMRQ": 45.0},
        "Technicals": {"Beta": 1.1},
    }
    return e


@pytest.fixture
def web(yahoo, eodhd):
    return FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo, eodhd)


async def _open(tmp_path, web, clock, **config):
    manager = open_context(Config(db_path=tmp_path / "o.db", **config), None, clock, web.transport)
    ctx = manager.__enter__()
    await refresh_universe(ctx)
    await update_listing(ctx, "AAPL")
    await update_fx(ctx)
    return manager, ctx


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
async def ctx(tmp_path, web, clock):
    manager, c = await _open(tmp_path, web, clock)
    yield c
    manager.__exit__(None, None, None)


@pytest.fixture
async def keyed(tmp_path, web, clock):
    manager, c = await _open(tmp_path, web, clock, keys=Keys({"eodhd": "k"}))
    yield c
    manager.__exit__(None, None, None)


def summary_calls(web):
    return [r for r in web.requests_to("query2.finance.yahoo.com") if "quoteSummary" in r.url.path]


async def test_the_profile_comes_from_the_universe_with_tags(ctx):
    add_tag(ctx, "AAPL", "big tech")
    view = await company_view(ctx, "apc.de")
    p = view.profile
    assert (p.symbol, p.primary_symbol, p.name, p.sector, p.listings, p.tags) == (
        "APC.DE", "AAPL", "Apple Inc.", "Information Technology", 2, ("big tech",)
    )
    assert p.website == "https://www.apple.com"


async def test_the_latest_price_shows_in_both_currencies_with_the_day_change(ctx):
    view = await company_view(ctx, "AAPL")
    assert (view.price.bar.close, view.price.close_base, view.change_pct) == (210.0, 168.0, pytest.approx(5.0))


async def test_fundamentals_are_fetched_normalised_and_stored(ctx):
    f = (await company_view(ctx, "AAPL")).fundamentals
    assert (f.source, f.as_of.isoformat(), f.currency) == ("yahoo", "2026-09-22", "USD")
    assert (f.pe, f.forward_pe, f.pb, f.dividend_yield, f.beta) == (39.0, 35.4, 46.2, 0.0032, 1.085)
    assert (f.profit_margin, f.roe, f.revenue_growth) == (0.276, 1.49, 0.164)
    assert f.debt_to_equity == pytest.approx(0.784)  # Yahoo reports it as a percentage


async def test_market_cap_converts_to_base_currency(ctx):
    view = await company_view(ctx, "AAPL")
    assert view.market_cap_base == pytest.approx(4.0e12 / 1.25)


async def test_a_pence_quoted_company_reports_market_cap_in_pounds(ctx):
    view = await company_view(ctx, "BP.L")
    assert (view.fundamentals.currency, view.fundamentals.market_cap, view.market_cap_base) == ("GBP", 8.4e10, 8.4e10)


async def test_missing_figures_stay_missing_not_zero(ctx):
    f = (await company_view(ctx, "BP.L")).fundamentals
    assert (f.pe, f.dividend_yield, f.beta, f.debt_to_equity) == (20.9, None, None, None)


async def test_a_second_view_the_same_day_makes_no_request(ctx, web):
    await company_view(ctx, "AAPL")
    await company_view(ctx, "AAPL")
    assert len(summary_calls(web)) == 1


async def test_the_next_day_the_snapshot_is_refreshed(ctx, web, clock):
    await company_view(ctx, "AAPL")
    clock.advance(24 * 3600)
    view = await company_view(ctx, "AAPL")
    assert len(summary_calls(web)) == 2 and view.fundamentals.as_of.isoformat() == "2026-09-23"


async def test_the_crumb_is_fetched_with_a_browser_user_agent(ctx, yahoo):
    await company_view(ctx, "AAPL")
    assert yahoo.crumb_user_agents and all("Mozilla" in ua for ua in yahoo.crumb_user_agents)


async def test_an_expired_crumb_is_refreshed_once(ctx, yahoo, web):
    yahoo.expire_crumb_after_issue = True
    view = await company_view(ctx, "AAPL")
    assert view.fundamentals.pe == 39.0
    assert yahoo.crumbs_issued == 2 and len(summary_calls(web)) == 2


async def test_without_fundamentals_the_view_still_shows_and_says_why(ctx, yahoo):
    yahoo.crumb_status = 429
    view = await company_view(ctx, "AAPL")
    assert view.fundamentals is None
    assert "crumb" in view.fundamentals_note and view.price is not None


async def test_eodhd_fills_in_when_keyed_and_yahoo_fails(keyed, yahoo):
    yahoo.crumb_status = 429
    f = (await company_view(keyed, "AAPL")).fundamentals
    assert (f.source, f.pe, f.forward_pe, f.pb, f.beta, f.debt_to_equity) == ("eodhd", 38.0, 34.0, 45.0, 1.1, None)
    assert f.revenue_growth == 0.16


async def test_a_failed_refresh_keeps_showing_the_last_snapshot(ctx, yahoo, clock):
    await company_view(ctx, "AAPL")
    clock.advance(24 * 3600)
    yahoo.crumb_status = 429
    view = await company_view(ctx, "AAPL")
    assert view.fundamentals.as_of.isoformat() == "2026-09-22"
    assert "2026-09-22" in view.fundamentals_note


async def test_every_figure_has_a_plain_english_line(ctx):
    from oracle.services.company_service import FIGURES

    assert all(len(f.meaning) > 30 for f in FIGURES)
    assert {f.name for f in FIGURES} >= {
        "market_cap", "pe", "forward_pe", "pb", "dividend_yield", "profit_margin", "roe",
        "revenue_growth", "debt_to_equity", "beta",
    }
