import asyncio


async def test_nothing_is_listed_before_any_work(client):
    assert (await client.get("/api/activity")).json() == {"jobs": [], "elsewhere": []}


async def test_an_unknown_job_is_a_404(client):
    response = await client.get("/api/activity/999")
    assert response.status_code == 404 and "no job 999" in response.json()["error"]


async def test_the_activity_tab_module_is_served(client):
    assert (await client.get("/static/tabs/activity.js")).status_code == 200


async def test_the_runner_stops_with_the_server_and_interrupts_running_work(ctx):
    from oracle.gui.app import build_app

    app = build_app(ctx)
    started = asyncio.Event()

    async def forever():
        started.set()
        await asyncio.Event().wait()

    async with app.router.lifespan_context(app):
        job = app.state.runner.submit("research", "Research X", forever)
        await started.wait()
    assert job.state == "interrupted"


async def test_work_another_process_holds_is_listed_read_only(ctx, client):
    import os

    from oracle.jobs import JobLock

    other = os.getppid()  # alive, and not this process
    JobLock(ctx.conn, ctx.clock, pid=other).acquire("prices_update", 600)
    [held] = (await client.get("/api/activity")).json()["elsewhere"]
    assert (held["job"], held["pid"]) == ("prices update", other)


async def test_jobs_an_earlier_server_finished_are_listed_without_their_results(ctx, client):
    from datetime import datetime, timezone

    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    at = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)
    for kind in ("prices", "analyse", "tick"):
        repo.update(repo.add(kind, kind, None, None, "queued", at, 1), "done", None, at, at)
    listed = (await client.get("/api/activity")).json()["jobs"]
    assert [(j["kind"], j["state"], j["result"]) for j in listed] == [
        ("tick", "done", None), ("analyse", "done", None), ("prices", "done", None)]
