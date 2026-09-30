from oracle.services.watch_service import watch


async def test_status_plan_and_the_job(loaded, ctx):
    watch(ctx, "AAPL")
    status = (await loaded.get("/api/reader")).json()
    assert status == {"model": "qwen3:4b-instruct-2507-q8_0", "last_run": None}
    plan = (await loaded.get("/api/reader/plan")).json()
    assert plan["model"] == "ollama:qwen3:4b-instruct-2507-q8_0" and plan["unscored"] == 0 and plan["rate"] > 0
    first = await loaded.post("/api/jobs/reader")
    again = await loaded.post("/api/jobs/reader?watchlist=main")
    assert first.status_code == 202 and first.json()["kind"] == "reader"
    assert again.json()["id"] == first.json()["id"]
    assert (await loaded.post("/api/jobs/reader?watchlist=nope")).status_code == 404
