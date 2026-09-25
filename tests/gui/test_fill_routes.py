from tests.gui.jobs import run_job


async def test_run_fill_pass_now_shows_apart_from_the_last_tick(ctx, loaded):
    status = (await loaded.get("/api/status")).json()
    assert status["tick"] is None and status["fill"] is None
    job = await run_job(loaded, "/api/jobs/fill")
    assert job["label"] == "Fill pass (manual)"
    run = job["result"]
    assert (run["kind"], run["label"], [s["name"] for s in run["stages"]]) == (
        "fill", "Fill pass", ["prices", "fx", "fill"])
    status = (await loaded.get("/api/status")).json()
    assert status["fill"] == run and status["tick"] is None


async def test_status_shows_the_fill_times_and_they_are_set_from_the_settings_form(ctx, client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    await client.put("/api/settings/schedule", json={"enabled": True, "time": "22:30"})
    schedule = (await client.get("/api/status")).json()["schedule"]
    assert schedule["fill_times"] == ["09:30", "15:15"] and schedule["fill_next"]
    await client.put("/api/settings", json={"section": "schedule", "key": "fill_times", "value": []})
    schedule = (await client.get("/api/status")).json()["schedule"]
    assert (schedule["fill_times"], schedule["fill_next"]) == ([], None)
    bad = await client.put("/api/settings", json={"section": "schedule", "key": "fill_times", "value": ["22:40"]})
    assert bad.status_code == 400 and "fill_times" in bad.json()["error"]
