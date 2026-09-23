from datetime import datetime, timezone

import httpx
import pytest

from oracle.config import Config
from oracle.gui.app import build_app
from oracle.services.context import open_context
from oracle.services.universe_service import refresh_universe
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def ctx(tmp_path):
    fdb = FakeFinanceDatabase(UNIVERSE)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), fdb.transport) as c:
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
