async def test_the_page_is_served_at_the_root(client):
    response = await client.get("/")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    assert "Market Oracle" in response.text


async def test_static_modules_are_served_and_do_not_shadow_the_api(client):
    assert (await client.get("/static/app.js")).status_code == 200
    response = await client.get("/api/status")
    assert response.headers["content-type"].startswith("application/json")


async def test_pages_and_modules_are_rechecked_so_an_update_shows_without_a_hard_reload(client):
    for path in ("/", "/static/app.js"):
        assert (await client.get(path)).headers["cache-control"] == "no-cache"


async def test_the_scheduler_runs_while_the_server_does_and_stops_with_it(ctx):
    from oracle.gui.app import build_app
    app = build_app(ctx, schedule=True)
    async with app.router.lifespan_context(app):
        task = app.state.scheduler_task
        assert not task.done()
    assert task.cancelled()
