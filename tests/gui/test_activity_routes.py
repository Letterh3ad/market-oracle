import asyncio


async def test_nothing_is_listed_before_any_work(client):
    assert (await client.get("/api/activity")).json() == {"jobs": []}


async def test_an_unknown_job_is_a_404(client):
    response = await client.get("/api/activity/999")
    assert response.status_code == 404 and "no job 999" in response.json()["error"]


async def test_the_activity_tab_module_is_served(client):
    assert (await client.get("/static/tabs/activity.js")).status_code == 200


async def test_the_runner_stops_with_the_server_and_cancels_running_work(ctx):
    from oracle.gui.app import build_app

    app = build_app(ctx)
    started = asyncio.Event()

    async def forever():
        started.set()
        await asyncio.Event().wait()

    async with app.router.lifespan_context(app):
        job = app.state.runner.submit("research", "Research X", forever)
        await started.wait()
    assert job.state == "cancelled"
