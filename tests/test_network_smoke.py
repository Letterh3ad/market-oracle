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
