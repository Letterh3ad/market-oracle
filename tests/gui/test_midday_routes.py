from datetime import timedelta

from tests.gui.jobs import run_job


async def test_run_midday_pass_now_shows_apart_from_the_last_tick(ctx, loaded):
    status = (await loaded.get("/api/status")).json()
    assert status["tick"] is None and status["midday"] is None
    job = await run_job(loaded, "/api/jobs/midday")
    assert job["label"] == "Midday pass (manual)"
    run = job["result"]
    assert (run["kind"], run["label"], [s["name"] for s in run["stages"]]) == (
        "midday", "Midday pass", ["discover", "research"])
    status = (await loaded.get("/api/status")).json()
    assert status["midday"] == run and status["tick"] is None


async def test_status_shows_the_midday_slot_and_it_is_set_from_the_settings_form(ctx, client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    await client.put("/api/settings/schedule", json={"enabled": True, "time": "22:30"})
    schedule = (await client.get("/api/status")).json()["schedule"]
    assert (schedule["midday_time"], schedule["midday_budget_min"]) == ("12:30", 60) and schedule["midday_next"]
    await client.put("/api/settings", json={"section": "schedule", "key": "midday_time", "value": ""})
    await client.put("/api/settings", json={"section": "schedule", "key": "midday_budget_min", "value": 20})
    schedule = (await client.get("/api/status")).json()["schedule"]
    assert (schedule["midday_time"], schedule["midday_budget_min"], schedule["midday_next"]) == ("", 20, None)
    bad = await client.put("/api/settings", json={"section": "schedule", "key": "midday_time", "value": "noon"})
    assert bad.status_code == 400 and "midday_time" in bad.json()["error"]


async def test_the_warning_names_the_midday_pass(ctx, client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    soon = (ctx.clock.utcnow() + timedelta(minutes=5)).astimezone().strftime("%H:%M")
    later = (ctx.clock.utcnow() + timedelta(hours=3)).astimezone().strftime("%H:%M")
    await client.put("/api/settings", json={"section": "schedule", "key": "midday_time", "value": soon})
    await client.put("/api/settings/schedule", json={"enabled": True, "time": later})
    warning = (await client.get("/api/status")).json()["warning"]
    assert (warning["kind"], warning["label"]) == ("midday", "Midday pass")
    skipped = (await client.post("/api/schedule/skip")).json()
    assert skipped["kind"] == "midday"
    assert (await client.get("/api/status")).json()["midday"]["note"] == "skipped: skipped by the user"
