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

    async def __call__(self):
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


async def test_stop_cancels_running_work(runner):
    gate = Gate("a", [])
    job = runner.submit("research", "Research A", gate)
    await settle()
    await runner.stop()
    assert job.state == "cancelled"


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
