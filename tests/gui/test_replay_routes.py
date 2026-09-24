import pytest

from oracle.store.replay import ReplayRepo
from tests.gui.conftest import DAYS
from tests.gui.jobs import wait_job
from tests.fakes.yahoo import bar


@pytest.fixture
async def listed(loaded, yahoo):
    """A year of prices for three listings of the fake universe, not yet fetched."""
    for symbol in ("AAPL", "7203.T", "KO"):
        yahoo.add(symbol, "USD", "America/New_York", [bar(d.isoformat(), 10.0) for d in DAYS])
    return loaded


async def create(client, **body) -> dict:
    r = await client.post("/api/replay", json=body)
    assert r.status_code == 202, r.json()
    return await wait_job(client, r.json())


async def test_no_study_yet(loaded):
    assert (await loaded.get("/api/replay")).json() == {"study": None, "cutoff": None}


async def test_create_is_a_job_then_paused_until_start_then_pause(listed):
    job = await create(listed, first="2026-06-01", symbols=["AAPL", "7203.T"])
    assert (job["kind"], job["state"], job["link"]) == ("replay", "done", "#ledgers")
    created = job["result"]
    assert created["state"] == "paused" and created["first_day"] == "2026-06-01"
    assert [p["symbol"] for p in created["listings"]] == ["AAPL", "7203.T"]
    assert created["total"] == 6 and created["pending"] == 6
    assert (await listed.post("/api/replay/start")).json()["state"] == "running"
    s = (await listed.get("/api/replay")).json()["study"]
    assert (s["state"], s["done"], s["running"], s["failed"], s["top_failures"]) == ("running", 0, [], [], [])
    assert (s["mean_replay_s"], s["complete"], s["finished"], s["polled_at"]) == (None, False, False, None)
    assert (await listed.post("/api/replay/pause")).json()["state"] == "paused"


async def test_a_listing_is_replaced_before_the_first_replay(listed):
    await create(listed, first="2026-06-01", symbols=["AAPL", "7203.T"])
    r = await listed.post("/api/replay/replace", json={"old": "7203.T", "new": "KO"})
    assert r.status_code == 202
    job = await wait_job(listed, r.json())
    assert [p["symbol"] for p in job["result"]["listings"]] == ["AAPL", "KO"]


async def test_a_listing_without_prices_fails_the_job_with_the_reason(listed):
    job = await create(listed, first="2026-06-01", symbols=["005930.KS"])
    assert job["state"] == "failed" and "no price data for 005930.KS" in job["error"]


async def test_create_without_a_day_or_cutoff_is_refused_at_once(loaded):
    r = await loaded.post("/api/replay", json={})
    assert r.status_code == 400 and "--from" in r.json()["error"]


async def test_start_without_a_study_is_refused(loaded):
    r = await loaded.post("/api/replay/start")
    assert r.status_code == 400 and "create one first" in r.json()["error"]


async def test_retry_one_cell_or_all_failed(ctx, listed):
    await create(listed, first="2026-06-01", symbols=["AAPL", "7203.T"])
    repo = ReplayRepo(ctx.conn)
    a, b = repo.cells(1)[:2]
    for c in (a, b):
        for _ in range(c.allowed):
            repo.mark_failed(c.id, "model down", ctx.clock.utcnow())
    s = (await listed.get("/api/replay")).json()["study"]
    assert [f["id"] for f in s["failed"]] == [a.id, b.id] and s["top_failures"] == [["model down", 2]]
    assert (await listed.post("/api/replay/retry", json={"cell_id": a.id})).json() == {"moved": 1}
    assert (await listed.post("/api/replay/retry", json={})).json() == {"moved": 1}
    r = await listed.post("/api/replay/retry", json={"cell_id": a.id})
    assert r.status_code == 400
