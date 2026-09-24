import asyncio
import threading


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
    started = threading.Event()  # set on the job's worker thread

    async def forever(c):
        started.set()
        await asyncio.Event().wait()

    async with app.router.lifespan_context(app):
        job = app.state.runner.submit("research", "Research X", forever)
        await asyncio.to_thread(started.wait)
    assert job.state == "interrupted"


async def test_work_another_process_holds_is_listed_read_only(ctx, client):
    import os

    from oracle.jobs import JobLock

    other = os.getppid()  # alive, and not this process
    JobLock(ctx.conn, ctx.clock, pid=other).acquire("prices_update", 600)
    [held] = (await client.get("/api/activity")).json()["elsewhere"]
    assert (held["job"], held["pid"]) == ("prices update", other)


async def test_jobs_an_earlier_server_finished_are_listed_without_their_results(finish_job, client):
    for n, kind in enumerate(("prices", "analyse", "tick")):
        finish_job(n, kind)
    listed = (await client.get("/api/activity/finished")).json()["items"]
    assert [(j["kind"], j["state"], j["result"]) for j in listed] == [
        ("tick", "done", None), ("analyse", "done", None), ("prices", "done", None)]


async def test_finished_jobs_come_a_page_at_a_time_and_stay_out_of_the_live_list(finish_job, client):
    for n in range(25):
        finish_job(n)
    body = (await client.get("/api/activity/finished", params={"page": 2})).json()
    assert (body["page"], body["pages"], body["total"]) == (2, 2, 25)
    assert [j["label"] for j in body["items"]] == [f"Job {n}" for n in range(4, -1, -1)]
    assert (await client.get("/api/activity")).json() == {"jobs": [], "elsewhere": []}


async def test_finished_jobs_can_be_searched(finish_job, client):
    finish_job(1, label="Research MSFT")
    finish_job(2, label="Research AAPL")
    body = (await client.get("/api/activity/finished", params={"q": "msft"})).json()
    assert [j["label"] for j in body["items"]] == ["Research MSFT"] and body["total"] == 1


async def test_finished_jobs_can_be_counted_alone(finish_job, client):
    finish_job(1, label="Research MSFT")
    finish_job(2, label="Research AAPL")
    assert (await client.get("/api/activity/finished/count")).json() == {"total": 2}
    assert (await client.get("/api/activity/finished/count", params={"q": "aapl"})).json() == {"total": 1}


async def test_a_search_matching_nothing_is_an_empty_page(client):
    body = (await client.get("/api/activity/finished", params={"q": "zzz"})).json()
    assert body == {"items": [], "page": 1, "pages": 1, "total": 0}


async def test_a_page_below_one_is_refused(client):
    assert (await client.get("/api/activity/finished", params={"page": 0})).status_code in (400, 422)
