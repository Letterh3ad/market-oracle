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
