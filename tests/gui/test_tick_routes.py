from oracle.services.jobs_service import single_flight


async def test_run_tick_now_returns_each_stage_and_status_shows_it(priced):
    assert (await priced.get("/api/status")).json()["tick"] is None
    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    tick = (await priced.post("/api/jobs/tick")).json()
    assert [s["name"] for s in tick["stages"]] == ["prices", "fx", "fill", "analyse", "step"]
    assert (tick["trigger"], tick["outcome"], tick["first_error"]) == ("manual", "ok", None)
    assert (await priced.get("/api/status")).json()["tick"] == tick


async def test_a_second_tick_while_one_runs_is_refused(ctx, loaded):
    with single_flight(ctx, "tick", 60.0):
        response = await loaded.post("/api/jobs/tick")
    assert response.status_code == 409 and "tick is already running" in response.json()["error"]
