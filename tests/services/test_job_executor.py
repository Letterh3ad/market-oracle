import asyncio
import threading
import time
from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.job_executor import ThreadExecutor
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW)) as c:
        yield c


async def test_work_runs_on_another_thread_with_its_own_connection(ctx):
    async def work(c):
        return threading.get_ident(), c.conn is ctx.conn, c.conn.execute("SELECT 41 + 1").fetchone()[0], c.clock

    thread, same_conn, answer, clock = await ThreadExecutor(ctx).run(work)
    assert thread != threading.get_ident() and not same_conn and answer == 42 and clock is ctx.clock


async def test_the_server_loop_keeps_answering_while_a_job_blocks(ctx):
    async def blocking(c):
        time.sleep(0.5)

    job = asyncio.create_task(ThreadExecutor(ctx).run(blocking))
    ticks = 0
    while not job.done():
        await asyncio.sleep(0.01)
        ticks += 1
    await job
    assert ticks > 10


async def test_a_failure_in_the_job_reaches_the_caller(ctx):
    async def failing(c):
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        await ThreadExecutor(ctx).run(failing)


async def test_cancelling_reaches_the_job_and_waits_for_its_cleanup(ctx):
    started, cleaned = threading.Event(), []

    async def long(c):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            await asyncio.sleep(0.05)  # cleanup that itself awaits, like releasing a lock
            cleaned.append(True)

    job = asyncio.create_task(ThreadExecutor(ctx).run(long))
    await asyncio.to_thread(started.wait)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert cleaned == [True]


async def test_the_runner_runs_jobs_on_the_thread_executor(ctx):
    from oracle.services.activity_service import JobRunner

    runner = JobRunner(ctx, ThreadExecutor(ctx))

    async def work(c):
        return threading.get_ident()

    job = runner.submit("prices", "Prices", work)
    await runner.wait(job.id)
    assert job.state == "done" and job.result != threading.get_ident()
    await runner.stop()


async def test_a_second_cancel_still_waits_for_the_jobs_cleanup(ctx):
    started, cleaned = threading.Event(), []

    async def long(c):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            time.sleep(0.3)  # cleanup stuck in a synchronous stretch
            cleaned.append(True)

    job = asyncio.create_task(ThreadExecutor(ctx).run(long))
    await asyncio.to_thread(started.wait)
    job.cancel()
    await asyncio.sleep(0.05)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert cleaned == [True]
