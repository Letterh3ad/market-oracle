from datetime import datetime, timezone

import pytest

from oracle.config import AnalystsConfig, Config
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.peers_service import peer_group
from oracle.services.universe_service import refresh_universe
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity, etf
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, summary

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
SEMIS = dict(sector="Information Technology", industry_group="Semiconductors & Semiconductor Equipment")


def chip(symbol, name, cap, **extra):
    return equity(symbol, name, currency="USD", market_cap=cap, **{**SEMIS, **extra})


UNIVERSE = [
    chip("NVDA", "NVIDIA", "Mega Cap"),
    chip("AMD", "Advanced Micro Devices", "Large Cap"),
    chip("INTC", "Intel", "Large Cap"),
    chip("MU", "Micron", "Large Cap"),
    chip("TXN", "Texas Instruments", "Large Cap"),
    chip("QCOM", "Qualcomm", "Mega Cap"),
    chip("TINY", "Tiny Chips", "Nano Cap"),
    equity("MSFT", "Microsoft", currency="USD", market_cap="Mega Cap", sector="Information Technology",
           industry_group="Software & Services"),
    equity("ORCL", "Oracle", currency="USD", market_cap="Mega Cap", sector="Information Technology",
           industry_group="Software & Services"),
    equity("XOM", "Exxon", currency="USD", market_cap="Mega Cap", sector="Energy", industry_group="Energy"),
]


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    for row in UNIVERSE:
        y.summaries[row["symbol"]] = summary("USD", market_cap=1e11, trailingPE=20.0)
    return y


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def web(yahoo):
    return FakeWeb(FakeFinanceDatabase(UNIVERSE, [etf("SMH", "Semis ETF")]), yahoo)


@pytest.fixture
async def ctx(tmp_path, web, clock):
    manager = open_context(Config(db_path=tmp_path / "o.db", analysts=AnalystsConfig(peers=5)), None, clock,
                           web.transport)
    c = manager.__enter__()
    await refresh_universe(c)
    yield c
    manager.__exit__(None, None, None)


def summary_calls(web):
    return [r for r in web.requests_to("query2.finance.yahoo.com") if "quoteSummary" in r.url.path]


async def test_peers_are_the_biggest_in_the_industry_group_without_the_company_itself(ctx):
    group = await peer_group(ctx, "amd")
    assert [p.symbol for p in group.peers] == ["NVDA", "QCOM", "INTC", "MU", "TXN"]
    assert "Semiconductors" in group.basis and not group.fell_back
    assert all(p.fundamentals is not None and p.fundamentals.pe == 20.0 for p in group.peers)


async def test_a_thin_industry_group_falls_back_to_the_sector_and_says_so(ctx):
    group = await peer_group(ctx, "MSFT")
    assert group.fell_back and "sector Information Technology" in group.basis and "too few" in group.basis
    assert "MSFT" not in [p.symbol for p in group.peers] and len(group.peers) == 5


async def test_peer_fundamentals_are_reused_for_a_week(ctx, web, clock):
    await peer_group(ctx, "AMD")
    first = len(summary_calls(web))
    clock.advance(7 * 86400)
    await peer_group(ctx, "AMD")
    assert len(summary_calls(web)) == first
    clock.advance(86400)
    await peer_group(ctx, "AMD")
    assert len(summary_calls(web)) == first + 5


async def test_a_peer_without_fundamentals_is_listed_as_missing_and_the_rest_load(ctx, yahoo):
    del yahoo.summaries["INTC"]
    peers = {p.symbol: p for p in (await peer_group(ctx, "AMD")).peers}
    assert peers["INTC"].fundamentals is None and "no fundamentals" in peers["INTC"].note
    assert peers["MU"].fundamentals is not None


async def test_a_fund_has_no_peers(ctx):
    with pytest.raises(InvalidQuery, match="fund"):
        await peer_group(ctx, "SMH")
