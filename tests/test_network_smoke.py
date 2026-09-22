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
