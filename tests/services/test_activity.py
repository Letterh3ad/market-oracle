import asyncio
from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.activity_service import JobRunner
from oracle.services.context import open_context
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW)) as c:
        yield c


@pytest.fixture
async def runner(ctx):
    r = JobRunner(ctx)
    yield r
    await r.stop()


class Gate:
    """Work that runs until the test opens it, recording when it started."""

    def __init__(self, name, log, fail=None):
        self.name, self.log, self.fail = name, log, fail
        self.open = asyncio.Event()

    async def __call__(self, ctx=None):
        self.log.append(f"start {self.name}")
        await self.open.wait()
        self.log.append(f"end {self.name}")
        if self.fail:
            raise self.fail
        return self.name


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


async def test_jobs_of_one_kind_run_in_order_never_together(runner):
    log = []
    a, b = Gate("a", log), Gate("b", log)
    ja = runner.submit("research", "Research A", a)
    jb = runner.submit("research", "Research B", b)
    await settle()
    assert (ja.state, jb.state) == ("running", "queued") and log == ["start a"]
    a.open.set()
    await settle()
    assert (ja.state, jb.state) == ("done", "running") and log == ["start a", "end a", "start b"]
    b.open.set()
    await runner.wait(jb.id)
    assert jb.state == "done" and jb.result == "b"


async def test_different_kinds_run_side_by_side(runner):
    log = []
    runner.submit("research", "Research A", Gate("a", log))
    runner.submit("prices", "Update prices", Gate("p", log))
    await settle()
    assert sorted(log) == ["start a", "start p"]


async def test_a_failed_job_records_its_error_and_the_queue_moves_on(runner):
    log = []
    bad, good = Gate("bad", log, fail=RuntimeError("model is down")), Gate("good", log)
    jbad = runner.submit("research", "Research bad", bad)
    jgood = runner.submit("research", "Research good", good)
    bad.open.set()
    good.open.set()
    await runner.wait(jgood.id)
    assert (jbad.state, jbad.error) == ("failed", "model is down") and jgood.state == "done"
    assert jbad.finished_at == NOW


async def test_progress_is_read_while_running(runner):
    gate = Gate("a", [])
    job = runner.submit("research", "Research A", gate, progress=lambda: "step 2: news_search")
    await settle()
    assert job.progress() == "step 2: news_search"
    gate.open.set()
    await runner.wait(job.id)
    assert job.progress() is None


async def test_listing_puts_running_first_then_queued_in_order_then_finished_newest_first(runner, ctx):
    gates = [Gate(n, []) for n in "abcd"]
    jobs = [runner.submit("research", f"Research {g.name}", g) for g in gates]
    gates[0].open.set()
    await runner.wait(jobs[0].id)
    await settle()
    assert [j.label for j in runner.jobs()] == ["Research b", "Research c", "Research d", "Research a"]


async def test_the_same_subject_already_waiting_is_not_queued_twice(runner):
    first = runner.submit("research", "Research A", Gate("a", []), subject="AAPL")
    again = runner.submit("research", "Research A", Gate("a2", []), subject="AAPL")
    assert again is first and len(runner.jobs()) == 1


async def test_cancelling_a_queued_job_means_it_never_runs(runner):
    log = []
    a, b = Gate("a", log), Gate("b", log)
    runner.submit("research", "Research A", a)
    jb = runner.submit("research", "Research B", b)
    runner.cancel(jb.id)
    a.open.set()
    await settle()
    assert jb.state == "cancelled" and "start b" not in log


async def test_cancelling_a_running_job_ends_it_and_the_next_one_starts(runner):
    log = []
    a, b = Gate("a", log), Gate("b", log)
    ja = runner.submit("research", "Research A", a)
    jb = runner.submit("research", "Research B", b)
    await settle()
    runner.cancel(ja.id)
    await settle()
    assert ja.state == "cancelled" and jb.state == "running" and "end a" not in log


async def test_a_finished_job_cannot_be_cancelled(runner):
    from oracle.services.errors import InvalidQuery

    gate = Gate("a", [])
    job = runner.submit("research", "Research A", gate)
    gate.open.set()
    await runner.wait(job.id)
    with pytest.raises(InvalidQuery, match="already done"):
        runner.cancel(job.id)


@pytest.fixture
def gated_stages(monkeypatch):
    """Tick stages that finish at once except `analyse`, which waits for the test."""
    from oracle.services import tick_service

    gate = asyncio.Event()

    async def quick(ctx):
        return "done", None

    async def slow(ctx):
        await gate.wait()
        return "done", None

    monkeypatch.setattr(tick_service, "STAGES", {"prices": quick, "fx": quick, "analyse": slow, "step": quick})
    return gate


async def test_a_tick_job_shows_the_stage_it_is_on(runner, ctx, gated_stages):
    from oracle.services.activity_service import submit_tick

    job = submit_tick(ctx, runner)
    await settle()
    assert job.progress() == "analyse"
    gated_stages.set()
    await runner.wait(job.id)
    assert job.result.outcome == "ok" and [s.name for s in job.result.stages][-1] == "step"


async def test_cancelling_a_running_tick_records_it_interrupted(runner, ctx, gated_stages):
    from oracle.services.activity_service import submit_tick
    from oracle.services.tick_service import last_tick

    job = submit_tick(ctx, runner)
    await settle()
    runner.cancel(job.id)
    await runner.wait(job.id)
    assert job.state == "cancelled" and last_tick(ctx).outcome == "interrupted"


def london_scheduler(ctx, runner):
    from dataclasses import replace
    from zoneinfo import ZoneInfo

    from oracle.config import ScheduleConfig
    from oracle.services.schedule_service import Scheduler

    # NOW is Wednesday 21:00 in London; the run is due at 21:30 there (20:30 UTC).
    ctx.config = replace(ctx.config, schedule=ScheduleConfig(enabled=True, time="21:30"))
    return Scheduler(ctx, runner, tz=ZoneInfo("Europe/London"))


async def test_a_due_tick_runs_as_a_job_listed_in_activity(runner, ctx):
    scheduler = london_scheduler(ctx, runner)
    ctx.clock.advance(31 * 60)
    run = await scheduler.check()
    assert run.trigger == "scheduled"
    assert [(j.label, j.state) for j in runner.jobs()] == [("Tick (scheduled)", "done")]


async def test_a_due_tick_is_skipped_while_one_is_running_here(runner, ctx, gated_stages):
    from oracle.services.activity_service import submit_tick

    scheduler = london_scheduler(ctx, runner)
    manual = submit_tick(ctx, runner)
    await settle()
    ctx.clock.advance(31 * 60)
    skipped = await scheduler.check()
    assert skipped.outcome == "skipped" and "already queued or running" in skipped.note
    gated_stages.set()
    await runner.wait(manual.id)


async def test_finished_jobs_survive_a_restart(ctx, runner):
    gate = Gate("a", [])
    job = runner.submit("research", "Research A", gate, link="#company/A")
    gate.open.set()
    await runner.wait(job.id)
    await runner.stop()
    later = JobRunner(ctx)
    [kept] = later.jobs()
    assert (kept.id, kept.label, kept.state, kept.link) == (job.id, "Research A", "done", "#company/A")
    assert later.get(job.id).finished_at == job.finished_at
    await later.stop()


DEAD_PID = 999_999_999


async def test_a_starting_server_sweeps_jobs_a_stopped_server_left_queued_or_running(ctx):
    from oracle.services.activity_service import sweep_leftovers
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    running = repo.add("research", "Research A", None, None, "running", NOW, DEAD_PID)
    queued = repo.add("research", "Research B", None, None, "queued", NOW, DEAD_PID)
    sweep_leftovers(ctx)
    runner = JobRunner(ctx)
    states = {j.id: (j.state, j.error) for j in runner.jobs()}
    assert states[running] == ("interrupted", "interrupted: app closed")
    assert states[queued] == ("cancelled", "not run: app closed")
    await runner.stop()


async def test_the_sweep_leaves_another_live_servers_jobs_and_they_cannot_be_cancelled_here(ctx, runner, monkeypatch):
    from oracle.services import activity_service
    from oracle.services.activity_service import sweep_leftovers
    from oracle.services.errors import InvalidQuery
    from oracle.store.job_history import JobHistoryRepo

    other = JobHistoryRepo(ctx.conn).add("research", "Research A", "A", None, "queued", NOW, 4242)
    monkeypatch.setattr(activity_service, "pid_alive", lambda pid: pid == 4242)
    sweep_leftovers(ctx)
    assert runner.get(other).state == "queued"
    with pytest.raises(InvalidQuery, match="another copy of the app"):
        runner.cancel(other)


async def test_a_job_running_when_the_server_stops_is_interrupted_not_cancelled(ctx):
    runner = JobRunner(ctx)
    job = runner.submit("research", "Research A", Gate("a", []))
    waiting = runner.submit("research", "Research B", Gate("b", []))
    await settle()
    await runner.stop()
    assert (job.state, job.error) == ("interrupted", "interrupted: app closed")
    assert (waiting.state, waiting.error) == ("cancelled", "not run: app closed")


async def test_activity_lists_only_the_last_50_finished(ctx, runner):
    from oracle.store.job_history import JobHistoryRepo

    repo = JobHistoryRepo(ctx.conn)
    for n in range(55):
        job_id = repo.add("prices", f"Job {n}", None, None, "queued", NOW, DEAD_PID)
        repo.update(job_id, "done", None, NOW, NOW)
    listed = runner.jobs()
    assert len(listed) == 50 and listed[0].label == "Job 54"


async def test_waiting_on_an_unknown_job_says_so(runner):
    from oracle.services.errors import UnknownJob

    with pytest.raises(UnknownJob):
        await runner.wait(12345)


async def test_a_scheduled_tick_cancelled_before_it_starts_is_recorded_so_it_is_not_resubmitted(runner, ctx):
    from oracle.services.tick_service import last_tick

    scheduler = london_scheduler(ctx, runner)
    ctx.clock.advance(31 * 60)
    check = asyncio.create_task(scheduler.check())
    await asyncio.sleep(0)  # the check has submitted; the runner has not started it yet
    [queued] = runner.active("tick")
    assert queued.state == "queued"
    runner.cancel(queued.id)
    run = await check
    assert run.outcome == "skipped" and "cancelled" in run.note and last_tick(ctx).id == run.id
    assert await scheduler.check() is None


async def test_new_job_ids_follow_the_stored_ones(ctx, runner):
    first = runner.submit("research", "Research A", Gate("a", []))
    await runner.stop()
    later = JobRunner(ctx)
    assert later.submit("research", "Research B", Gate("b", [])).id > first.id
    await later.stop()
