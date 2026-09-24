from oracle.store.replay import ReplayRepo


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
