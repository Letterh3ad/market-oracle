import os
from datetime import datetime, timezone

import httpx
import pytest

from oracle.config import Config
from oracle.jobs import JobLock
from oracle.services.context import open_context
from oracle.services.errors import JobAlreadyRunning, UniverseUnavailable
from oracle.services.net_service import source_statuses
from oracle.services import universe_service
from oracle.services.universe_service import refresh_universe, universe_status
from oracle.store.universe import UniverseRepo
from oracle.universe.corrections import Correction
from oracle.universe.regions import home_line
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import APPLE, BASIC, EQUITY_COLUMNS, FakeFinanceDatabase, bz2_csv, equity

START = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def fdb():
    return FakeFinanceDatabase(BASIC)


@pytest.fixture
def ctx(tmp_path, fdb):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(START), fdb.transport) as c:
        yield c


def test_status_before_any_refresh_says_not_loaded(ctx):
    s = universe_status(ctx)
    assert not s.loaded
    assert s.listings == 0


async def test_refresh_loads_listings_and_counts_companies(ctx):
    s = await refresh_universe(ctx)
    assert (s.loaded, s.listings, s.companies, s.delisted) == (True, 7, 4, 1)
    assert s.source_commit == "abc123"
    assert s.refreshed_at >= START
    assert universe_status(ctx) == s


async def test_refresh_downloads_the_commit_it_resolved(ctx, fdb):
    await refresh_universe(ctx)
    downloads = [r.url for r in fdb.requests if r.url.host == "raw.githubusercontent.com"]
    assert sorted(u.path for u in downloads) == [
        "/JerBouma/FinanceDatabase/abc123/compression/equities.bz2",
        "/JerBouma/FinanceDatabase/abc123/compression/etfs.bz2",
    ]


async def test_refresh_goes_through_the_financedatabase_gate(ctx):
    await refresh_universe(ctx)
    gate = {s.source: s for s in source_statuses(ctx)}["financedatabase"]
    assert gate.requests_last_hour == 3


async def test_a_second_refresh_replaces_rather_than_appends(ctx, fdb):
    await refresh_universe(ctx)
    fdb.sha = "def456"
    fdb.files["equities.bz2"] = bz2_csv([equity("NEW", "Newco Inc.")])
    s = await refresh_universe(ctx)
    assert (s.listings, s.companies, s.source_commit) == (1, 1, "def456")


@pytest.mark.parametrize("broken", [
    500,
    404,
    bz2_csv(BASIC)[:-200],  # truncated download
    b"not bz2 at all",
])
async def test_a_failed_refresh_keeps_the_previous_universe(ctx, fdb, broken):
    before = await refresh_universe(ctx)
    fdb.sha = "def456"
    fdb.files["equities.bz2"] = broken
    with pytest.raises(UniverseUnavailable):
        await refresh_universe(ctx)
    assert universe_status(ctx) == before


async def test_an_unreadable_commit_lookup_fails_cleanly(ctx, fdb):
    fdb.commit_response = httpx.Response(200, json={"message": "API rate limit exceeded"})
    with pytest.raises(UniverseUnavailable, match="commit"):
        await refresh_universe(ctx)


async def test_a_file_missing_columns_is_refused_by_name(ctx, fdb):
    columns = [c for c in EQUITY_COLUMNS if c != "country"]
    fdb.files["equities.bz2"] = bz2_csv(BASIC, columns)
    with pytest.raises(UniverseUnavailable, match="country"):
        await refresh_universe(ctx)


async def test_refresh_refuses_while_another_process_holds_it(ctx):
    other = JobLock(ctx.conn, ctx.clock, pid_alive=lambda pid: True, pid=os.getpid() + 1)
    other.acquire("universe_refresh", lease_s=600)
    with pytest.raises(JobAlreadyRunning, match="universe refresh is already running"):
        await refresh_universe(ctx)
    assert not universe_status(ctx).loaded


async def test_refresh_releases_its_lock_after_a_failure(ctx, fdb):
    fdb.files["equities.bz2"] = 500
    with pytest.raises(UniverseUnavailable):
        await refresh_universe(ctx)
    fdb.files["equities.bz2"] = bz2_csv(BASIC)
    assert (await refresh_universe(ctx)).loaded


async def test_refresh_names_each_stage_as_it_starts(ctx):
    stages = []
    await refresh_universe(ctx, on_stage=stages.append)
    assert stages == ["downloading", "saving"]


TSMC_ADR = equity("TSM", "Taiwan Semiconductor Manufacturing Company Limited", "Taiwan", "NYQ", currency="USD",
                  isin="US8740391003", sector="Information Technology", market_cap="Mega Cap",
                  market="New York Stock Exchange")


async def test_a_home_line_financedatabase_lacks_is_added_beside_its_company(tmp_path):
    fdb = FakeFinanceDatabase([*BASIC, TSMC_ADR])
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(START), fdb.transport) as c:
        await refresh_universe(c)
        repo = UniverseRepo(c.conn)
        tsmc = repo.resolve("2330.TW")
        assert tsmc is not None and tsmc.company_key == repo.resolve("TSM").company_key
        profile = repo.profile("2330.TW")
        assert (profile.exchange, tsmc.currency, profile.market_cap) == ("TAI", "TWD", "Mega Cap")
        assert home_line(repo.company_listings(tsmc.company_key), {"Taiwan"}).symbol == "2330.TW"


async def test_a_missing_home_line_is_added_only_when_absent_and_its_company_is_there(tmp_path):
    own = equity("2330.TW", "TSMC", "Taiwan", "TAI", currency="TWD", isin="US8740391003", market_cap="Large Cap")
    for rows, band in (([*BASIC, TSMC_ADR, own], "Large Cap"), (BASIC, None)):
        with open_context(Config(db_path=tmp_path / f"{band}.db"), None, FakeClock(START),
                          FakeFinanceDatabase(rows).transport) as c:
            await refresh_universe(c)
            profile = UniverseRepo(c.conn).profile("2330.TW")
            assert (profile.market_cap if profile else None) == band


async def test_a_refresh_names_the_correction_keys_matching_no_company(ctx, monkeypatch, caplog):
    fixes = {f"isin:{APPLE}|equity": Correction("Apple Inc.", "Information Technology", "Technology Hardware & Equipment",
                                                "Technology Hardware, Storage & Peripherals"),
             "name:gone plc|United Kingdom|equity": Correction("Gone plc", "Industrials", "Capital Goods", "Machinery")}
    monkeypatch.setattr(universe_service, "CORRECTIONS", fixes)
    status = await refresh_universe(ctx)
    assert status.stale_corrections == ("Gone plc (name:gone plc|United Kingdom|equity)",)
    assert "Gone plc (name:gone plc|United Kingdom|equity)" in caplog.text
