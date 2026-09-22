"""Real FinanceDatabase download, skipped by default: catches upstream format changes."""

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
