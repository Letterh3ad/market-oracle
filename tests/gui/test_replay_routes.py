from datetime import date

from oracle.store.replay import ReplayRepo
from tests.fakes.yahoo import bar
from tests.gui.conftest import DAYS
from tests.gui.jobs import run_job


async def test_no_study_yet(loaded):
    assert (await loaded.get("/api/replay")).json() == {"study": None, "cutoff": None}


async def test_create_is_paused_until_start_then_pause(loaded):
    created = (await loaded.post("/api/replay", json={"first": "2026-06-01"})).json()
    assert created["state"] == "paused" and created["first_day"] == "2026-06-01"
    assert [p["symbol"] for p in created["listings"]] == ["AAPL", "7203.T", "005930.KS"]
    assert created["total"] == 9 and created["pending"] == 9
    assert (await loaded.post("/api/replay/start")).json()["state"] == "running"
    body = (await loaded.get("/api/replay")).json()
    s = body["study"]
    assert (s["state"], s["done"], s["running"], s["failed"], s["top_failures"]) == ("running", 0, [], [], [])
    assert s["mean_replay_s"] is None and s["complete"] is False
    assert (await loaded.post("/api/replay/pause")).json()["state"] == "paused"


async def test_create_without_a_day_or_cutoff_is_refused(loaded):
    r = await loaded.post("/api/replay", json={})
    assert r.status_code == 400 and "--from" in r.json()["error"]


async def test_start_without_a_study_is_refused(loaded):
    r = await loaded.post("/api/replay/start")
    assert r.status_code == 400 and "create one first" in r.json()["error"]


async def test_retry_one_cell_or_all_failed(ctx, loaded):
    await loaded.post("/api/replay", json={"first": "2026-06-01"})
    repo = ReplayRepo(ctx.conn)
    a, b = repo.cells(1)[:2]
    for c in (a, b):
        repo.mark_failed(c.id, "model down", ctx.clock.utcnow(), requeue=False)
    s = (await loaded.get("/api/replay")).json()["study"]
    assert [f["id"] for f in s["failed"]] == [a.id, b.id] and s["top_failures"] == [["model down", 2]]
    assert (await loaded.post("/api/replay/retry", json={"cell_id": a.id})).json() == {"moved": 1}
    assert (await loaded.post("/api/replay/retry", json={})).json() == {"moved": 1}
    r = await loaded.post("/api/replay/retry", json={"cell_id": a.id})
    assert r.status_code == 400


async def test_report_scores_from_stored_bars_and_the_status_carries_the_interim_block(loaded):
    await loaded.post("/api/replay", json={"first": "2026-06-01"})
    report = (await loaded.get("/api/replay/report")).json()["report"]
    assert report["verdicts"] == 0 and [h["horizon"] for h in report["horizons"]] == ["1m", "3m"]
    assert report["refetch_listings"] == ["AAPL", "7203.T", "005930.KS"] and report["limits"]
    score = (await loaded.get("/api/replay")).json()["study"]["score"]
    assert score["verdicts"] == 0 and score["horizons"]["1m"]["scored"] == 0


async def test_update_prices_is_a_job_fetching_only_what_the_score_lacks(loaded, yahoo):
    yahoo.add("AAPL", "USD", "America/New_York", [bar(d.isoformat(), 100 + n) for n, d in enumerate(DAYS)])
    await loaded.post("/api/replay", json={"first": "2026-06-01"})
    job = await run_job(loaded, "/api/replay/prices")
    assert job["kind"] == "replay_prices" and job["state"] == "done"
    fetched = {o["symbol"]: o for o in job["result"]["listings"]}
    in_window = [d for d in DAYS if d >= date(2026, 5, 25)]  # the study window, from a week before its first day
    assert fetched["AAPL"]["added"] == len(in_window) and fetched["7203.T"]["error"]
    report = (await loaded.get("/api/replay/report")).json()["report"]
    assert report["refetch_listings"] == ["7203.T", "005930.KS"]
    assert report["price_failures"] == ["7203.T", "005930.KS"] and report["prices_checked_at"]


async def test_report_without_a_study(loaded):
    assert (await loaded.get("/api/replay/report")).json() == {"report": None}
    assert (await loaded.post("/api/replay/prices")).status_code == 400
