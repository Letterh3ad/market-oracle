from dataclasses import replace

from oracle.services.jobs_service import single_flight
from tests.gui.jobs import run_job


async def test_run_tick_now_returns_each_stage_and_status_shows_it(ctx, priced):
    ctx.config = replace(ctx.config, discovery=replace(ctx.config.discovery, enabled=False))
    assert (await priced.get("/api/status")).json()["tick"] is None
    await priced.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    tick = (await run_job(priced, "/api/jobs/tick"))["result"]
    assert [s["name"] for s in tick["stages"]] == ["prices", "fx", "fill", "analyse", "index_step", "regime_step", "regime_ai_step", "regime_v2_step", "core_sat_step", "laya_step", "discover", "research", "step",
        "ai_step", "industry"]
    assert (tick["trigger"], tick["outcome"], tick["first_error"]) == ("manual", "ok", None)
    assert (await priced.get("/api/status")).json()["tick"] == tick


async def test_a_tick_another_process_is_running_fails_saying_so(ctx, loaded):
    with single_flight(ctx, "tick", 60.0):
        job = await run_job(loaded, "/api/jobs/tick")
    assert job["state"] == "failed" and "tick is already running" in job["error"]
