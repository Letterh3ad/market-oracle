"""The scheduler's second slot: the midday pass at 12:30 local time, which never fires late."""

from dataclasses import replace

import pytest

from oracle.services.jobs_service import single_flight
from oracle.services.settings_service import set_automation
from oracle.services.tick_service import last_midday, last_tick, run_midday, run_tick
from oracle.store.ticks import TickRepo, TickRun
from tests.services.test_schedule import MONDAY_EVENING, ctx, scheduler, utc  # noqa: F401

TUESDAY_MIDDAY = utc(2026, 9, 22, 11, 30)  # 12:30 BST


@pytest.fixture(autouse=True)
def midday_on(ctx):
    schedule(ctx, midday_time="12:30")


def at(ctx, when):
    ctx.clock.advance((when - ctx.clock.utcnow()).total_seconds())


def schedule(ctx, **values):
    ctx.config = replace(ctx.config, schedule=replace(ctx.config.schedule, **values))


async def ticked(ctx, s):
    """Monday's 22:30 tick fires, so a later poll can only be the midday pass."""
    at(ctx, utc(2026, 9, 21, 21, 30))
    assert (await s.check()).kind == "tick"


async def test_a_due_midday_pass_fires_once_and_never_counts_as_the_tick(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    tick = last_tick(ctx)
    at(ctx, TUESDAY_MIDDAY)
    run = await s.check()
    assert (run.kind, run.trigger, [st.name for st in run.stages]) == ("midday", "scheduled", ["discover", "questions", "research"])
    assert await s.check() is None
    assert last_tick(ctx).id == tick.id and last_midday(ctx).id == run.id


async def test_off_when_its_time_is_empty_or_scheduling_or_automation_is_off(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    schedule(ctx, midday_time="")
    at(ctx, TUESDAY_MIDDAY)
    assert await s.check() is None
    schedule(ctx, midday_time="12:30")
    set_automation(ctx, False)
    ctx.clock.advance(60)
    assert await s.check() is None
    set_automation(ctx, True)
    assert await s.check() is None and last_midday(ctx) is None  # fell due while off: not fired late


async def test_a_pass_missed_while_the_app_was_closed_never_catches_up(ctx):
    await run_tick(ctx)
    at(ctx, utc(2026, 9, 22, 13, 0))  # opened Tuesday 14:00 BST
    s = scheduler(ctx)
    assert (await s.check()).trigger == "catch-up"  # Monday's tick
    assert await s.check() is None and last_midday(ctx) is None  # closed at its time: no record either


async def test_a_restart_inside_the_grace_still_runs_that_days_pass(ctx):
    await run_tick(ctx)
    at(ctx, utc(2026, 9, 22, 11, 40))  # opened Tuesday 12:40 BST
    s = scheduler(ctx)
    assert (await s.check()).kind == "tick"  # Monday's catch-up first
    run = await s.check()
    assert (run.kind, run.trigger, run.outcome) == ("midday", "catch-up", "ok")


async def test_a_pass_the_app_slept_through_is_not_fired_late(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, utc(2026, 9, 22, 12, 1))  # 13:01 BST: past its half-hour grace
    missed = await s.check()
    assert (missed.kind, missed.stages) == ("midday", ())
    assert missed.note == "missed: not started within 30 minutes of its time"
    assert await s.check() is None
    at(ctx, utc(2026, 9, 23, 11, 30))
    assert (await s.check()).kind == "tick"  # Tuesday's, first
    assert (await s.check()).kind == "midday"


async def test_a_few_minutes_late_it_still_fires(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, utc(2026, 9, 22, 11, 50))
    assert (await s.check()).kind == "midday"


async def test_a_midday_pass_today_never_hides_a_missed_tick(ctx):
    await run_tick(ctx)  # Monday 21:00 BST, then closed over the 22:30 tick
    at(ctx, TUESDAY_MIDDAY)
    await run_midday(ctx, "scheduled")  # say another process ran it
    ctx.clock.advance(3600)
    assert (await scheduler(ctx).check()).trigger == "catch-up"


async def test_finding_a_run_holding_the_lock_it_is_recorded_skipped(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, TUESDAY_MIDDAY)
    with single_flight(ctx, "tick", 60.0):
        skipped = await s.check()
    assert (skipped.kind, skipped.outcome) == ("midday", "skipped") and "already running" in skipped.note
    assert await s.check() is None


async def test_the_warning_covers_the_midday_pass_and_skip_and_postpone_act_on_it(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, utc(2026, 9, 22, 11, 21))
    warned = s.warning()
    assert (warned.kind, warned.due) == ("midday", TUESDAY_MIDDAY)
    moved = s.postpone()
    assert (moved.kind, moved.at) == ("midday", utc(2026, 9, 22, 12, 30))
    at(ctx, TUESDAY_MIDDAY)
    assert await s.check() is None
    at(ctx, utc(2026, 9, 22, 12, 30))
    assert (await s.check()).kind == "midday"
    assert s.upcoming().kind == "tick"  # Tuesday's 22:30 is next
    at(ctx, utc(2026, 9, 22, 21, 30))
    assert (await s.check()).kind == "tick"
    at(ctx, utc(2026, 9, 23, 11, 25))
    skipped = s.skip()
    assert (skipped.kind, skipped.finished_at, skipped.outcome) == ("midday", utc(2026, 9, 23, 11, 30), "skipped")
    at(ctx, utc(2026, 9, 23, 11, 30))
    assert await s.check() is None and s.upcoming().kind == "tick"


async def test_a_postponed_pass_left_unrun_stops_being_upcoming_once_missed(ctx):
    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, utc(2026, 9, 22, 11, 25))
    s.postpone()
    at(ctx, utc(2026, 9, 22, 13, 1))  # slept through 13:30 BST and its grace
    assert (await s.check()).note.startswith("missed")
    assert s.upcoming().kind == "tick" and s.warning() is None


async def test_changing_the_midday_pass_keeps_a_postponed_tick(ctx):
    s = scheduler(ctx)
    at(ctx, utc(2026, 9, 21, 21, 25))
    assert s.postpone().kind == "tick"
    at(ctx, utc(2026, 9, 21, 21, 45))
    assert await s.check() is None
    schedule(ctx, midday_time="11:00")
    assert await s.check() is None
    at(ctx, utc(2026, 9, 21, 22, 30))
    assert (await s.check()).kind == "tick"


@pytest.fixture
def long_tick(monkeypatch):
    """A tick whose only stage takes 40 minutes of the fake clock."""
    from oracle.services import tick_service

    async def slow(c, say):
        c.clock.advance(40 * 60)
        return "done", None

    monkeypatch.setattr(tick_service, "STAGES", {"prices": slow})


async def test_a_pass_queued_behind_a_long_tick_is_missed_if_it_starts_past_its_grace(ctx, long_tick):
    from oracle.services.activity_service import submit_tick

    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, TUESDAY_MIDDAY)
    submit_tick(ctx, s._runner)  # a manual tick, pressed just before
    missed = await s.check()
    assert (missed.kind, missed.note) == ("midday", "missed: started too late")
    assert await s.check() is None


async def test_a_pass_due_but_waiting_is_what_the_banner_postpones_or_skips(ctx, long_tick):
    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, utc(2026, 9, 22, 11, 35))  # due five minutes ago, not yet started
    up = s.upcoming()
    assert (up.kind, up.due, up.at) == ("midday", TUESDAY_MIDDAY, TUESDAY_MIDDAY) and s.warning() == up
    assert s.skip().kind == "midday"
    assert await s.check() is None and s.upcoming().kind == "tick"


async def test_a_tick_due_while_a_midday_pass_is_queued_waits_behind_it(ctx):
    from oracle.services.activity_service import submit_midday

    schedule(ctx, time="12:30", midday_time="13:00")
    s = scheduler(ctx)
    submit_midday(ctx, s._runner)
    at(ctx, TUESDAY_MIDDAY)
    run = await s.check()
    assert run.kind == "tick" and run.trigger == "scheduled"


@pytest.mark.parametrize("kind", ["tick", "midday"])
def test_a_stored_kind_reads_back(ctx, kind):
    run = TickRun("scheduled", MONDAY_EVENING, MONDAY_EVENING, (), "skipped: x", kind=kind)
    TickRepo(ctx.conn).save(run)
    back = TickRepo(ctx.conn).recent(1)[0]
    assert (back.kind, back.trigger) == (kind, "scheduled")


@pytest.fixture
def gated_midday(monkeypatch):
    """Midday passes whose one stage waits for the test."""
    import asyncio

    from oracle.services import tick_service

    gate = asyncio.Event()

    async def held(c, say):
        await gate.wait()
        return "done", None

    monkeypatch.setattr(tick_service, "_midday_stages", lambda started: {"discover": held})
    return gate


async def settle():
    import asyncio

    for _ in range(20):
        await asyncio.sleep(0)


async def test_a_pass_in_flight_is_not_offered_for_postpone_or_skip(ctx, gated_midday):
    import asyncio

    s = scheduler(ctx)
    await ticked(ctx, s)
    at(ctx, TUESDAY_MIDDAY)
    poll = asyncio.create_task(s.check())
    await settle()
    ctx.clock.advance(5 * 60)
    assert s.upcoming().kind == "tick" and s.warning() is None
    gated_midday.set()
    assert (await poll).kind == "midday"


async def test_a_tick_soon_after_a_running_pass_gets_the_banner(ctx, gated_midday):
    import asyncio

    schedule(ctx, time="12:50")  # 11:50 UTC, twenty minutes after the pass
    s = scheduler(ctx)
    at(ctx, TUESDAY_MIDDAY)
    poll = asyncio.create_task(s.check())
    await settle()
    at(ctx, utc(2026, 9, 22, 11, 45))
    warned = s.warning()
    assert (warned.kind, warned.due) == ("tick", utc(2026, 9, 22, 11, 50))
    assert s.postpone().kind == "tick"
    gated_midday.set()
    assert (await poll).kind == "midday"
