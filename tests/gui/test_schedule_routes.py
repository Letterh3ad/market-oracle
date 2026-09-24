from dataclasses import replace

import pytest


@pytest.fixture(autouse=True)
def midday_off(request, ctx):
    """These are about the tick (tests/gui/test_midday_routes.py covers the midday pass), bar
    the one named for running with the default midday pass on."""
    if "default_midday" not in request.node.name:
        ctx.config = replace(ctx.config, schedule=replace(ctx.config.schedule, midday_time=""))


async def test_with_the_default_midday_pass_on_the_banner_names_and_skips_the_run_it_shows(ctx, client, tmp_path,
                                                                                         monkeypatch):
    monkeypatch.chdir(tmp_path)
    await client.put("/api/settings/schedule", json={"enabled": True, "time": local_time(ctx, 20)})
    schedule = (await client.get("/api/status")).json()["schedule"]
    assert schedule["midday_time"] == "12:30" and schedule["midday_next"]
    ctx.clock.advance(12 * 60)
    warning = (await client.get("/api/status")).json()["warning"]
    assert warning["kind"] in ("tick", "midday")
    skipped = (await client.post("/api/schedule/skip")).json()
    assert skipped["kind"] == warning["kind"] and skipped["note"] == "skipped: skipped by the user"


async def test_scheduling_is_off_until_turned_on_and_then_shows_the_next_run(ctx, client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    schedule = (await client.get("/api/status")).json()["schedule"]
    assert (schedule["enabled"], schedule["time"], schedule["next_run"], schedule["watched"]) == (
        False, "22:30", None, 0)
    body = (await client.put("/api/settings/schedule", json={"enabled": True, "time": "21:00"})).json()
    assert (body["enabled"], body["time"]) == (True, "21:00") and body["next_run"]
    assert (await client.get("/api/status")).json()["schedule"] == body
    assert 'time = "21:00"' in (tmp_path / "oracle.toml").read_text(encoding="utf-8")


async def test_a_bad_time_is_refused(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = await client.put("/api/settings/schedule", json={"enabled": True, "time": "9pm"})
    assert response.status_code == 400 and "22:30" in response.json()["error"]


def local_time(ctx, minutes):
    """The machine-local "HH:MM" `minutes` after the fake clock, as the GUI's scheduler reads it."""
    from datetime import timedelta

    return (ctx.clock.utcnow() + timedelta(minutes=minutes)).astimezone().strftime("%H:%M")


async def test_a_tick_due_soon_is_warned_of_then_postponed_once(ctx, client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    await client.put("/api/settings/schedule", json={"enabled": True, "time": local_time(ctx, 20)})
    assert (await client.get("/api/status")).json()["warning"] is None
    ctx.clock.advance(12 * 60)
    warning = (await client.get("/api/status")).json()["warning"]
    assert warning["due"] == warning["at"] and warning["postponed"] is False
    moved = (await client.post("/api/schedule/postpone")).json()
    assert moved["postponed"] is True and moved["due"] == warning["due"]
    assert (await client.get("/api/status")).json()["warning"] is None
    again = await client.post("/api/schedule/postpone")
    assert again.status_code == 400 and "once" in again.json()["error"]


async def test_skip_today_is_recorded_as_the_last_tick(ctx, client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    await client.put("/api/settings/schedule", json={"enabled": True, "time": local_time(ctx, 5)})
    skipped = (await client.post("/api/schedule/skip")).json()
    assert (skipped["outcome"], skipped["note"]) == ("skipped", "skipped: skipped by the user")
    status = (await client.get("/api/status")).json()
    assert status["warning"] is None and status["tick"]["note"] == "skipped: skipped by the user"


async def test_nothing_to_skip_while_scheduling_is_off(client):
    response = await client.post("/api/schedule/skip")
    assert response.status_code == 400 and "no scheduled tick" in response.json()["error"]


async def test_the_warning_and_idle_settings_are_saved(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    body = (await client.put("/api/settings/tick-warning",
                             json={"warn_minutes": 0, "research_when_idle": True, "idle_minutes": 30})).json()
    assert (body["warn_minutes"], body["research_when_idle"], body["idle_minutes"]) == (0, True, 30)
    assert (await client.get("/api/status")).json()["schedule"] == body
    assert "idle_minutes = 30" in (tmp_path / "oracle.toml").read_text(encoding="utf-8")
    bad = await client.put("/api/settings/tick-warning",
                           json={"warn_minutes": 500, "research_when_idle": True, "idle_minutes": 30})
    assert bad.status_code == 400 and "warn_minutes" in bad.json()["error"]


async def test_a_running_tick_shows_with_its_stage_and_stops_as_interrupted(ctx, monkeypatch):
    import asyncio

    import httpx

    from oracle.gui.app import build_app
    from oracle.services import tick_service
    from oracle.services.tick_service import last_tick

    async def stuck(c, say):
        await asyncio.Event().wait()

    async def quick(c, say):
        return "done", None

    app = build_app(ctx)
    transport = httpx.ASGITransport(app=app)
    monkeypatch.setattr(tick_service, "STAGES", {"prices": quick, "analyse": stuck})
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        job = (await client.post("/api/jobs/tick")).json()
        for _ in range(500):
            running = (await client.get("/api/status")).json()["running_tick"]
            if running and running["stage"] == "analyse":
                break
            await asyncio.sleep(0.01)
        assert running == {"id": job["id"], "label": "Tick (manual)", "state": "running", "stage": "analyse"}
        stopped = (await client.post(f"/api/activity/{job['id']}/cancel")).json()
        assert stopped["state"] == "cancelled" and (await client.get("/api/status")).json()["running_tick"] is None
    assert last_tick(ctx).outcome == "interrupted"
