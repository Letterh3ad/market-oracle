from datetime import datetime, timedelta, timezone

import httpx
import pytest

from oracle.config import Config
from oracle.gui.app import build_app
from oracle.services.context import open_context
from oracle.services.universe_service import refresh_universe
from tests.analysts.bars import geometric
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
# 300 weekdays ending the day before NOW, so the prices are current.
DAYS = sorted(d for d in (NOW.date() - timedelta(n) for n in range(1, 430)) if d.weekday() < 5)[-300:]


@pytest.fixture
def yahoo():
    """Empty until a test adds series; prices and FX both come from it."""
    return FakeYahoo()


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def ctx(tmp_path, yahoo, clock):
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo)
    with open_context(Config(db_path=tmp_path / "o.db"), None, clock, web.transport) as c:
        yield c


@pytest.fixture
async def client(ctx):
    # In-process ASGI keeps each request on the test's own event loop thread, so the
    # context's SQLite connection is used from the thread that opened it, as in production.
    transport = httpx.ASGITransport(app=build_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://oracle.test") as c:
        yield c


@pytest.fixture
async def loaded(ctx, client):
    """A client over the fake FinanceDatabase universe (see tests/fakes/financedatabase.py)."""
    await refresh_universe(ctx)
    return client


@pytest.fixture
async def priced(loaded, yahoo):
    """AAPL (USD) with a year of rising prices and a GBP rate for every day, not yet fetched."""
    yahoo.add("AAPL", "USD", "America/New_York", [bar(d.isoformat(), c) for d, c in zip(DAYS, geometric(300, 0.001))])
    yahoo.add("GBPUSD=X", "USD", "Europe/London", [bar(d.isoformat(), 1.25) for d in DAYS])
    return loaded
