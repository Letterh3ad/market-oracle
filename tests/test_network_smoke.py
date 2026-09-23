"""Real third-party calls, skipped by default (run with -m network): catch upstream changes."""

import os

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.universe_service import refresh_universe

pytestmark = pytest.mark.network


async def test_real_financedatabase_refresh(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None) as ctx:
        s = await refresh_universe(ctx)
    assert s.listings > 100_000
    assert 30_000 < s.companies < s.listings


async def test_real_yahoo_daily_bars(tmp_path):
    from oracle.services.prices_service import price_history, update_listing

    with open_context(Config(db_path=tmp_path / "o.db"), None) as ctx:
        await refresh_universe(ctx)
        result = await update_listing(ctx, "BP.L")
        bars = price_history(ctx, "BP.L")
    assert result.added > 1000  # ~5 years of trading days
    assert bars[-1].currency == "GBP" and 1 < bars[-1].close < 20  # pounds, not pence


async def test_real_eodhd_with_the_public_demo_key(tmp_path):
    from oracle.config import Keys, PricesConfig
    from oracle.services.prices_service import update_listing

    config = Config(db_path=tmp_path / "o.db", keys=Keys({"eodhd": "demo"}), prices=PricesConfig(("eodhd",)))
    with open_context(config, None) as ctx:
        await refresh_universe(ctx)
        result = await update_listing(ctx, "AAPL")
    assert result.source == "eodhd" and result.added > 1000


@pytest.mark.skipif(not os.environ.get("ORACLE_EODHD_KEY"), reason="needs a paid EODHD key")
@pytest.mark.parametrize("symbol", ["BP.L", "SAP.DE", "7203.T", "0700.HK", "BHP.AX", "SHOP.TO"])
async def test_real_eodhd_agrees_with_yahoo_across_exchanges(tmp_path, symbol):
    # Verifies the exchange mapping and minor units, which the demo key cannot reach.
    from oracle.config import Keys, PricesConfig
    from oracle.services.prices_service import price_history, update_listing

    keys = Keys({"eodhd": os.environ["ORACLE_EODHD_KEY"]})
    closes = {}
    for source in ("yahoo", "eodhd"):
        config = Config(db_path=tmp_path / f"{source}.db", keys=keys, prices=PricesConfig((source,)))
        with open_context(config, None) as ctx:
            await refresh_universe(ctx)
            await update_listing(ctx, symbol)
            closes[source] = {b.date: b.close for b in price_history(ctx, symbol, days=10)}
    shared = sorted(set(closes["yahoo"]) & set(closes["eodhd"]))
    assert shared, "no common trading day"
    day = shared[-1]
    assert closes["eodhd"][day] == pytest.approx(closes["yahoo"][day], rel=0.03)


@pytest.mark.parametrize("source, currency", [
    ("yahoo", "USD"), ("ecb", "USD"), ("ecb", "EUR"), ("frankfurter", "JPY"), ("open_er_api", "TWD"),
])
async def test_real_fx_sources(tmp_path, source, currency):
    from datetime import timedelta

    from oracle.market import fx
    from oracle.services.http import open_client

    build = {"yahoo": fx.YahooFx, "ecb": fx.EcbFx, "frankfurter": fx.FrankfurterFx, "open_er_api": fx.OpenErFx}
    with open_context(Config(db_path=tmp_path / "o.db"), None) as ctx:
        now = ctx.clock.utcnow()
        async with open_client(ctx) as client:
            rates = await build[source](client).daily_rates("GBP", currency, now.date() - timedelta(days=14), now)
    assert rates and all(r.source == source and r.units_per_base > 0 for r in rates)
    sane = {"USD": (1.0, 2.0), "EUR": (1.0, 1.5), "JPY": (120, 260), "TWD": (30, 60)}[currency]
    assert sane[0] < rates[-1].units_per_base < sane[1]


async def test_real_yahoo_fundamentals_via_the_crumb():
    from datetime import date

    from oracle.market.fundamentals import YahooFundamentals
    from oracle.market.sources import Instrument
    from oracle.services.http import open_client

    with open_context(Config(db_path=":memory:"), None) as ctx:
        async with open_client(ctx) as client:
            f = await YahooFundamentals(client).fundamentals(Instrument("BP.L", "GBP"), date.today())
    assert f.currency == "GBP" and f.market_cap and 1e10 < f.market_cap < 1e12  # pounds, not pence
    assert f.pe is None or 0 < f.pe < 500


async def test_real_ollama_answers_inside_the_research_schema():
    from oracle.config import LlmConfig
    from oracle.research.engine import step_schema
    from oracle.research.ollama import OllamaChat

    llm = LlmConfig()
    chat = OllamaChat(llm.model, llm.base_url, llm.timeout_s, llm.context)
    reply = await chat.reply([{"role": "user", "content": "Reply with action final and input 'ok'."}],
                             step_schema(["market_data", "final"]))
    assert isinstance(reply, dict) and reply["action"] in ("market_data", "final")


async def test_real_google_news_feed_parses_into_dated_articles():
    import httpx

    from oracle.research.news import GOOGLE_NEWS, google_news_params, parse_google_news

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        resp = await http.get(GOOGLE_NEWS, params=google_news_params("Apple Inc"))
    articles = parse_google_news(resp.text)
    assert len(articles) > 5
    assert all(a.outlet and not a.title.endswith(f" - {a.outlet}") for a in articles)
    assert all(a.published is not None and a.published.tzinfo is not None for a in articles)


async def test_real_gdelt_doc_api_answers_in_artlist_json():
    import httpx

    from oracle.research.news import GDELT_DOC, gdelt_params, parse_gdelt

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
        resp = await http.get(GDELT_DOC, params=gdelt_params('"Apple Inc"'))
    if resp.status_code == 429:
        # A quick rerun lengthens GDELT's lockout; wait a few minutes before trying again.
        pytest.skip("GDELT is throttling this address")
    articles = parse_gdelt(resp.text)
    assert len(articles) > 5
    assert all(a.outlet and a.published is not None for a in articles)


async def test_real_ddgs_web_search_returns_links_with_snippets():
    from oracle.research.web import DdgsSearcher

    results = await DdgsSearcher().search("Apple Inc investor relations", 5)
    assert results and all(r.url.startswith("http") and r.title for r in results)


async def test_real_page_reads_as_text_without_scripts():
    import httpx

    from oracle.research.web import page_text

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        resp = await http.get("https://example.com/")
    title, text = page_text(resp.text)
    assert title == "Example Domain" and "documentation" in text and "{" not in text


async def test_real_sec_edgar_lists_apples_filings():
    import httpx

    from oracle.research import edgar

    contact = os.environ.get("ORACLE_SEC_CONTACT")
    if not contact:
        pytest.skip('set ORACLE_SEC_CONTACT="Name email" to check EDGAR live')
    headers = {"User-Agent": edgar.user_agent(contact)}
    async with httpx.AsyncClient(timeout=30, headers=headers) as http:
        tickers = edgar.parse_tickers((await http.get(edgar.TICKERS_URL)).json())
        cik, _ = tickers["AAPL"]
        filings = edgar.parse_filings((await http.get(edgar.submissions_url(cik))).json(), cik)
    assert cik == 320193 and any(f.form == "10-Q" for f in filings)
    assert all(f.url.startswith(edgar.ARCHIVES) for f in filings)
