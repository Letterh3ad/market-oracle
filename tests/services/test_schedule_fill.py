"""The scheduler's fill passes: weekdays at each fill time, never in the way of a tick."""

from dataclasses import replace

import pytest

from oracle.services.activity_service import submit_tick
from oracle.services.jobs_service import single_flight
from oracle.services.schedule_service import schedule_status
from oracle.services.tick_service import last_fill, last_tick
from tests.services.test_schedule import ctx, scheduler, utc  # noqa: F401

TUESDAY_0930 = utc(2026, 9, 22, 8, 30)  # BST
TUESDAY_1515 = utc(2026, 9, 22, 14, 15)


@pytest.fixture(autouse=True)
def fills_on(ctx):
    schedule(ctx, fill_times=("09:30", "15:15"))


def at(ctx, when):
    ctx.clock.advance((when - ctx.clock.utcnow()).total_seconds())


def schedule(ctx, **values):
    ctx.config = replace(ctx.config, schedule=replace(ctx.config.schedule, **values))


async def ticked(ctx, s):
    at(ctx, utc(2026, 9, 21, 21, 30))
    assert (await s.check()).kind == "tick"


async def test_each_fill_time_fires_once_on_a_weekday_and_never_counts_as_the_tick(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    tick = last_tick(ctx)
    at(ctx, TUESDAY_0930)
    run = await s.check()
    assert (run.kind, run.trigger, [st.name for st in run.stages]) == ("fill", "scheduled", ["prices", "fx", "fill"])
    assert await s.check() is None
    at(ctx, TUESDAY_1515)
    assert (await s.check()).kind == "fill"
    assert await s.check() is None
    assert last_tick(ctx).id == tick.id and last_fill(ctx).started_at == TUESDAY_1515


async def test_no_fill_pass_at_the_weekend(ctx):
    at(ctx, utc(2026, 9, 25, 21, 0))  # opened Friday evening
    s = scheduler(ctx)
    at(ctx, utc(2026, 9, 25, 21, 30))
    assert (await s.check()).kind == "tick"
    at(ctx, utc(2026, 9, 26, 8, 30))  # Saturday 09:30 BST
    assert await s.check() is None
    at(ctx, utc(2026, 9, 27, 14, 15))  # Sunday 15:15 BST
    assert await s.check() is None and last_fill(ctx) is None
    assert s.next_run().at == utc(2026, 9, 28, 8, 30)


async def test_off_with_no_fill_times(ctx):
    schedule(ctx, fill_times=())
    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, TUESDAY_0930)
    assert await s.check() is None and last_fill(ctx) is None


async def test_a_fill_time_finding_a_tick_running_here_is_skipped_not_queued(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, TUESDAY_0930)
    submit_tick(ctx, s._runner)  # a manual tick, queued just before
    skipped = await s.check()
    assert (skipped.kind, skipped.outcome) == ("fill", "skipped") and "tick" in skipped.note
    assert await s.check() is None


async def test_a_fill_time_finding_a_regime_step_queued_runs_after_it(ctx):
    from oracle.services.activity_service import submit_line_step

    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, TUESDAY_0930)
    step = submit_line_step(ctx, s._runner, "regime")
    run = await s.check()
    assert (run.kind, run.outcome) == ("fill", "ok") and step.state == "done"


async def test_a_fill_time_finding_the_lock_held_elsewhere_is_skipped(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, TUESDAY_0930)
    with single_flight(ctx, "tick", 60.0):
        skipped = await s.check()
    assert (skipped.kind, skipped.outcome) == ("fill", "skipped")


async def test_a_morning_pass_the_app_slept_through_never_hides_the_afternoon_one(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, utc(2026, 9, 22, 14, 20))  # woke at 15:20 BST
    missed = await s.check()
    assert (missed.kind, missed.finished_at) == ("fill", TUESDAY_0930) and missed.note.startswith("missed")
    run = await s.check()
    assert (run.kind, run.outcome) == ("fill", "ok")


async def test_the_banner_never_warns_of_a_fill_pass_but_the_next_run_names_it(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, utc(2026, 9, 22, 8, 25))
    assert s.warning() is None and s.upcoming().kind != "fill"
    assert (s.next_run().kind, s.next_run().at) == ("fill", TUESDAY_0930)


def test_the_status_lists_the_fill_times_and_the_next_one(ctx):
    view = schedule_status(ctx)
    assert view.fill_times == ("09:30", "15:15")
    assert view.fill_next is not None
    schedule(ctx, fill_times=())
    assert schedule_status(ctx).fill_next is None
