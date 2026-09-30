from oracle.services.watch_service import watch


async def test_status_plan_and_the_job(loaded, ctx):
    watch(ctx, "AAPL")
    status = (await loaded.get("/api/reader")).json()
    assert status == {"model": "qwen2.5:32b", "last_run": None}
    plan = (await loaded.get("/api/reader/plan?own_only=true")).json()
    assert plan["model"] == "ollama:qwen2.5:32b" and plan["unscored"] == 0 and plan["rate"] == 1.02
    assert (await loaded.get("/api/reader/plan")).status_code == 400, "map pairs come with ticket 06"
    first = await loaded.post("/api/jobs/reader?own_only=true")
    again = await loaded.post("/api/jobs/reader?watchlist=main&own_only=true")
    assert first.status_code == 202 and first.json()["kind"] == "reader"
    assert again.json()["id"] == first.json()["id"]
    assert (await loaded.post("/api/jobs/reader?watchlist=nope")).status_code == 404
