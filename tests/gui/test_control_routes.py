import asyncio
import os
import threading

import httpx
import pytest

from oracle.services.app_service import AppControl
from oracle.system.launch import LaunchCommand


@pytest.fixture
def control():
    events = []
    c = AppControl(LaunchCommand(("python", "-m", "oracle", "gui"), "."), spawn=lambda cmd: events.append("spawn"))
    c.on_exit = lambda: events.append("exit")
    c.events = events
    return c


@pytest.fixture
async def app_client(ctx, control):
    from oracle.gui.app import build_app

    app = build_app(ctx, control=control)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            c.app = app
            yield c


async def test_restart_starts_a_copy_stops_this_server_and_names_the_work_it_cuts_off(app_client, control):
    started = threading.Event()

    async def forever(c):
        started.set()
        await asyncio.Event().wait()

    app_client.app.state.runner.submit("research", "Research X", forever)
    await asyncio.to_thread(started.wait)
    response = await app_client.post("/api/app/restart")
    assert response.status_code == 202 and response.json() == {"restarting": True, "cut_off": ["Research X"]}
    assert control.events == ["spawn", "exit"]


async def test_status_names_this_server_so_the_page_can_tell_a_new_one(app_client):
    server = (await app_client.get("/api/status")).json()["server"]
    assert server["pid"] == os.getpid() and server["started_at"]


async def test_a_restart_that_cannot_start_a_copy_says_why(ctx):
    from oracle.gui.app import build_app

    def broken(cmd):
        raise OSError("no such interpreter")

    control = AppControl(LaunchCommand(("python",), "."), spawn=broken)
    control.on_exit = lambda: None
    app = build_app(ctx, control=control)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/app/restart")
    assert response.status_code == 409
    assert "no such interpreter" in response.json()["error"]


async def test_status_gives_the_running_version_and_the_one_on_disk(ctx):
    from oracle.gui.app import build_app

    versions = iter(["abc1234", "def5678"])
    control = AppControl(LaunchCommand(("python",), "."), spawn=lambda c: None, read_version=lambda: next(versions))
    app = build_app(ctx, control=control)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        server = (await c.get("/api/status")).json()["server"]
    assert (server["version"], server["version_on_disk"]) == ("abc1234", "def5678")


async def test_the_start_at_login_toggle_reads_and_sets_the_launcher(ctx, tmp_path):
    from oracle.gui.app import build_app
    from oracle.services.app_service import Autostart

    cmd = LaunchCommand(("python",), ".")
    control = AppControl(cmd, spawn=lambda c: None, read_version=lambda: "abc1234",
                         autostart=Autostart(tmp_path, cmd, write=lambda link, c, d: link.write_text("lnk")))
    app = build_app(ctx, control=control)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/app/autostart")).json()["enabled"] is False
        assert (await c.put("/api/app/autostart", json={"enabled": True})).json()["enabled"] is True
        assert (tmp_path / "Market Oracle.lnk").exists()
        assert (await c.put("/api/app/autostart", json={"enabled": False})).json()["enabled"] is False


async def test_automation_switch_saves_and_can_stop_automatic_work(app_client, ctx):
    started = threading.Event()

    async def forever(c):
        started.set()
        await asyncio.Event().wait()

    runner = app_client.app.state.runner
    runner.submit("tick", "Tick (scheduled)", forever, automatic=True)
    await asyncio.to_thread(started.wait)
    assert (await app_client.get("/api/status")).json()["automation"] == {"enabled": True}
    answer = (await app_client.put("/api/app/automation", json={"enabled": False, "stop_running": True})).json()
    assert answer == {"enabled": False, "stopped": ["Tick (scheduled)"]}
    assert ctx.config.automation.enabled is False
    assert (await app_client.get("/api/status")).json()["automation"] == {"enabled": False}


async def test_shut_down_stops_this_server_and_names_the_work_it_cuts_off(app_client, control):
    response = await app_client.post("/api/app/shutdown")
    assert response.status_code == 202 and response.json() == {"stopping": True, "cut_off": []}
    assert control.events == ["exit"]


async def test_shortcuts_are_installed_from_the_page(app_client, tmp_path, monkeypatch):
    from oracle.services import app_service

    monkeypatch.setattr(app_service, "shortcut_folders", lambda: [tmp_path / "Desktop"])
    monkeypatch.setattr(app_service, "write_shortcut", lambda link, cmd, d: None)
    answer = (await app_client.post("/api/app/shortcuts")).json()
    assert answer == {"written": [str(tmp_path / "Desktop" / "Market Oracle.lnk")]}
