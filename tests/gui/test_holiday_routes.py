import threading

import httpx
import pytest

from oracle.gui.app import build_app
from tests.cli.test_holiday import BAD, GOOD, FakeHoliday


@pytest.fixture
def fake():
    return FakeHoliday(GOOD)


@pytest.fixture
async def client(ctx, fake):
    app = build_app(ctx, holiday=fake)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.parametrize("method, path, command", [
    ("GET", "/api/holiday", "status"), ("POST", "/api/holiday/arm", "arm"), ("POST", "/api/holiday/disarm", "disarm"),
])
async def test_each_route_runs_its_command_on_a_worker_thread_and_connection(client, fake, ctx, method, path, command):
    seen = []
    original = getattr(fake, command)

    def spy(own):
        seen.append((threading.current_thread() is threading.main_thread(), own.conn is ctx.conn))
        return original(own)

    setattr(fake, command, spy)
    answer = (await client.request(method, path)).json()
    assert answer == GOOD.to_json()
    assert seen == [(False, False)]


async def test_a_refusal_is_an_answer_naming_the_checks(client, fake):
    fake.report = BAD
    answer = (await client.post("/api/holiday/arm")).json()
    assert answer["ok"] is False and answer["checks"][1] == {"name": "Sleep", "ok": False,
                                                             "detail": "sleeps after 30 min", "fix": "set Never"}


async def test_without_a_holiday_given_the_real_one_is_used_which_tests_refuse(ctx):
    app = build_app(ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://test") as c:
        assert (await c.get("/api/holiday")).status_code == 500
