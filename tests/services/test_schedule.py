import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from oracle.config import Config, ScheduleConfig
from oracle.services.activity_service import JobRunner
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.jobs_service import single_flight
from oracle.services.schedule_service import Scheduler, last_due, next_due, schedule_status
from oracle.services.settings_service import set_schedule
from oracle.services.tick_service import last_tick, run_tick
from tests.fakes.clock import FakeClock

LONDON = ZoneInfo("Europe/London")


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.mark.parametrize(("now", "last", "next_"), [
    # Friday 23:00 BST: Friday's run is behind, Monday's is next. Clocks go back on Sunday
    # 25 October, so Friday's 22:30 is 21:30 UTC and Monday's is 22:30 UTC.
    (utc(2026, 10, 23, 22, 0), utc(2026, 10, 23, 21, 30), utc(2026, 10, 26, 22, 30)),
    # The weekend runs nothing.
    (utc(2026, 10, 24, 12, 0), utc(2026, 10, 23, 21, 30), utc(2026, 10, 26, 22, 30)),
    # A minute before Monday's run: the last was Friday's.
    (utc(2026, 10, 26, 22, 29), utc(2026, 10, 23, 21, 30), utc(2026, 10, 26, 22, 30)),
    # On the minute: that run is due now.
    (utc(2026, 10, 26, 22, 30), utc(2026, 10, 26, 22, 30), utc(2026, 10, 27, 22, 30)),
    # Clocks go forward on Sunday 29 March: Friday 22:30 GMT, Monday 22:30 BST.
    (utc(2026, 3, 28, 9, 0), utc(2026, 3, 27, 22, 30), utc(2026, 3, 30, 21, 30)),
])
def test_due_times_are_weekdays_at_the_local_time_across_weekends_and_clock_changes(now, last, next_):
    assert last_due(now, "22:30", LONDON) == last
    assert next_due(now, "22:30", LONDON) == next_


MONDAY_EVENING = utc(2026, 9, 21, 20, 0)  # 21:00 BST, before the 22:30 run


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"  # mine\n', encoding="utf-8")
    # The midday pass is off here: tests/services/test_schedule_midday.py covers it.
    config = Config(db_path=tmp_path / "o.db", schedule=ScheduleConfig(enabled=True, midday_time=""))
    with open_context(config, path, FakeClock(MONDAY_EVENING)) as c:
        yield c


def scheduler(ctx):
    return Scheduler(ctx, JobRunner(ctx), tz=LONDON)


async def test_a_due_tick_fires_once_not_twice(ctx):
    s = scheduler(ctx)
    assert await s.check() is None
    ctx.clock.advance(90 * 60)  # 22:30 BST
    run = await s.check()
    assert run.trigger == "scheduled" and run.outcome == "ok"
    ctx.clock.advance(60)
    assert await s.check() is None


async def test_scheduling_off_fires_nothing(ctx):
    set_schedule(ctx, False, "22:30")
    s = scheduler(ctx)
    ctx.clock.advance(3 * 3600)
    assert await s.check() is None and last_tick(ctx) is None


async def test_a_due_tick_finding_one_running_is_skipped_and_says_so(ctx):
    s = scheduler(ctx)
    ctx.clock.advance(90 * 60)
    with single_flight(ctx, "tick", 60.0):
        skipped = await s.check()
    assert skipped.outcome == "skipped" and "already running" in skipped.note
    assert last_tick(ctx).id == skipped.id
    ctx.clock.advance(60)
    assert await s.check() is None


async def test_a_setting_change_applies_live_and_is_saved_with_comments(ctx):
    s = scheduler(ctx)
    set_schedule(ctx, True, "21:30")  # 20:30 UTC
    ctx.clock.advance(31 * 60)
    assert (await s.check()).trigger == "scheduled"
    text = ctx.config_source.read_text(encoding="utf-8")
    assert "# mine" in text and 'time = "21:30"' in text and "enabled = true" in text


async def test_turning_scheduling_on_after_todays_time_waits_for_the_next_one(ctx):
    set_schedule(ctx, False, "22:30")
    s = scheduler(ctx)
    ctx.clock.advance(2 * 3600)  # 23:00 BST, today's run already passed
    await s.check()
    set_schedule(ctx, True, "22:30")
    assert await s.check() is None


def test_a_bad_time_is_refused_and_nothing_is_written(ctx):
    before = ctx.config_source.read_text(encoding="utf-8")
    with pytest.raises(InvalidQuery, match="22:30"):
        set_schedule(ctx, True, "25:00")
    assert ctx.config_source.read_text(encoding="utf-8") == before and ctx.config.schedule.time == "22:30"


def test_status_shows_the_next_run_or_that_scheduling_is_off(ctx):
    view = schedule_status(ctx, tz=LONDON)
    assert (view.enabled, view.time, view.next_run) == (True, "22:30", utc(2026, 9, 21, 21, 30))
    set_schedule(ctx, False, "22:30")
    assert schedule_status(ctx, tz=LONDON).next_run is None


async def test_the_loop_polls_until_a_run_is_due(ctx):
    task = asyncio.create_task(scheduler(ctx).run())
    for _ in range(10_000):
        if last_tick(ctx):
            break
        await asyncio.sleep(0)
    task.cancel()
    assert last_tick(ctx).trigger == "scheduled"
    assert last_tick(ctx).started_at >= utc(2026, 9, 21, 21, 30)


async def test_closed_over_a_long_weekend_gives_one_catch_up_tick(ctx):
    await run_tick(ctx)  # Monday 21:00 BST, then the app closes until Wednesday week
    ctx.clock.advance(9 * 86400)
    s = scheduler(ctx)
    run = await s.check()
    assert run.trigger == "catch-up"
    assert await s.check() is None


async def test_closed_for_an_hour_before_the_due_time_gives_none(ctx):
    ctx.clock.advance(-86400)  # Sunday evening: Friday's run is the last due
    await run_tick(ctx)
    ctx.clock.advance(86400)  # back to Monday 21:00 BST, before today's run
    assert await scheduler(ctx).check() is None


async def test_no_catch_up_when_scheduling_is_off(ctx):
    set_schedule(ctx, False, "22:30")
    ctx.clock.advance(9 * 86400)
    assert await scheduler(ctx).check() is None and last_tick(ctx) is None


async def test_a_run_due_while_the_app_is_open_is_scheduled_not_catch_up(ctx):
    await run_tick(ctx)
    s = scheduler(ctx)
    ctx.clock.advance(90 * 60)
    assert (await s.check()).trigger == "scheduled"


async def test_no_catch_up_before_the_first_tick_ever(ctx):
    assert await scheduler(ctx).check() is None


async def test_a_schedule_saved_by_another_process_reaches_a_running_scheduler(ctx):
    import os

    s = scheduler(ctx)
    path = ctx.config_source
    path.write_text(path.read_text(encoding="utf-8") + '[schedule]\nenabled = true\ntime = "21:30"\n',
                    encoding="utf-8")
    os.utime(path, ns=(1, 1))  # a new stamp even on a coarse file clock
    ctx.clock.advance(31 * 60)
    assert (await s.check()).trigger == "scheduled" and ctx.config.schedule.time == "21:30"


async def test_a_file_that_stops_loading_keeps_the_running_schedule(ctx):
    import os

    s = scheduler(ctx)
    ctx.config_source.write_text("[schedule]\nenabled = maybe\n", encoding="utf-8")
    os.utime(ctx.config_source, ns=(1, 1))
    await s.check()
    assert ctx.config.schedule.enabled


async def test_a_skip_never_hides_the_longer_tick_it_ran_into(ctx):
    from oracle.store.ticks import Stage, TickRepo, TickRun

    s = scheduler(ctx)
    ctx.clock.advance(89 * 60)
    started = ctx.clock.utcnow()  # a manual tick starts a minute before the due time
    ctx.clock.advance(60)
    with single_flight(ctx, "tick", 60.0):
        assert (await s.check()).outcome == "skipped"
    ctx.clock.advance(300)
    TickRepo(ctx.conn).save(TickRun("manual", started, ctx.clock.utcnow(), (Stage("prices", "ok", "done"),)))
    assert last_tick(ctx).trigger == "manual"
    assert await s.check() is None


async def test_automation_off_means_a_due_tick_never_fires_and_on_again_waits_for_the_next(ctx):
    from oracle.services.settings_service import set_automation

    s = scheduler(ctx)
    set_automation(ctx, False)
    ctx.clock.advance(2 * 3600)  # 23:00 BST: the 22:30 run fell due while off
    assert await s.check() is None and last_tick(ctx) is None
    assert "[automation]" in ctx.config_source.read_text(encoding="utf-8")
    set_automation(ctx, True)
    assert await s.check() is None  # and does not fire late


async def test_automation_turned_off_in_another_process_reaches_a_running_scheduler(ctx):
    import os

    s = scheduler(ctx)
    path = ctx.config_source
    path.write_text(path.read_text(encoding="utf-8") + "[automation]\nenabled = false\n", encoding="utf-8")
    os.utime(path, ns=(1, 1))
    ctx.clock.advance(90 * 60)
    assert await s.check() is None and ctx.config.automation.enabled is False


def test_the_warning_shows_only_inside_its_window_before_the_due_time(ctx):
    s = scheduler(ctx)
    ctx.clock.advance(79 * 60)  # 22:19 BST, 11 minutes before
    assert s.warning() is None
    ctx.clock.advance(60)
    warned = s.warning()
    assert (warned.due, warned.at, warned.postponed) == (utc(2026, 9, 21, 21, 30), utc(2026, 9, 21, 21, 30), False)


def test_no_warning_when_it_is_off_or_scheduling_is_off(ctx):
    from dataclasses import replace

    s = scheduler(ctx)
    ctx.clock.advance(85 * 60)
    ctx.config = replace(ctx.config, schedule=replace(ctx.config.schedule, warn_minutes=0))
    assert s.warning() is None
    ctx.config = replace(ctx.config, schedule=replace(ctx.config.schedule, warn_minutes=10, enabled=False))
    assert s.warning() is None and s.upcoming() is None


async def test_postpone_moves_the_due_time_an_hour_once(ctx):
    s = scheduler(ctx)
    ctx.clock.advance(85 * 60)
    moved = s.postpone()
    assert (moved.at, moved.postponed) == (utc(2026, 9, 21, 22, 30), True)
    with pytest.raises(InvalidQuery, match="once"):
        s.postpone()
    assert s.warning() is None  # the banner returns 10 minutes before the new time
    ctx.clock.advance(6 * 60)  # 22:31 BST: the old time passes quietly
    assert await s.check() is None and last_tick(ctx) is None
    ctx.clock.advance(49 * 60)
    assert s.warning().at == utc(2026, 9, 21, 22, 30)
    with pytest.raises(InvalidQuery, match="once"):
        s.postpone()
    ctx.clock.advance(10 * 60)  # 23:30 BST
    assert (await s.check()).trigger == "scheduled"
    assert s.upcoming().due == utc(2026, 9, 22, 21, 30)  # tomorrow's is unaffected


async def test_skip_records_the_due_time_skipped_and_it_never_fires(ctx):
    s = scheduler(ctx)
    ctx.clock.advance(85 * 60)
    skipped = s.skip()
    assert skipped.outcome == "skipped" and skipped.note == "skipped: skipped by the user"
    assert skipped.finished_at == utc(2026, 9, 21, 21, 30)
    assert s.upcoming().due == utc(2026, 9, 22, 21, 30) and s.warning() is None
    ctx.clock.advance(10 * 60)
    assert await s.check() is None
    assert await scheduler(ctx).check() is None  # a restart still honours it
    assert last_tick(ctx).id == skipped.id


async def test_a_postponed_tick_can_still_be_skipped(ctx):
    s = scheduler(ctx)
    ctx.clock.advance(85 * 60)
    s.postpone()
    ctx.clock.advance(50 * 60)  # after the old time, before the new one
    assert s.skip().finished_at == utc(2026, 9, 21, 21, 30)
    ctx.clock.advance(30 * 60)
    assert await s.check() is None


def test_nothing_to_postpone_or_skip_while_scheduling_is_off(ctx):
    set_schedule(ctx, False, "22:30")
    s = scheduler(ctx)
    for act in (s.postpone, s.skip):
        with pytest.raises(InvalidQuery, match="no scheduled tick"):
            act()


def test_changing_the_warning_keeps_the_schedule_and_is_saved(ctx):
    from oracle.services.settings_service import set_tick_warning

    from dataclasses import replace

    ctx.config = replace(ctx.config, schedule=replace(ctx.config.schedule, midday_time="11:45", midday_budget_min=7))
    set_tick_warning(ctx, 5, True, 20)
    s = ctx.config.schedule
    assert (s.midday_time, s.midday_budget_min) == ("11:45", 7)
    assert (s.enabled, s.time, s.warn_minutes, s.research_when_idle, s.idle_minutes) == (True, "22:30", 5, True, 20)
    set_schedule(ctx, True, "21:00")
    assert ctx.config.schedule.warn_minutes == 5
    assert "warn_minutes = 5" in ctx.config_source.read_text(encoding="utf-8")
    with pytest.raises(InvalidQuery, match="warn_minutes"):
        set_tick_warning(ctx, 500, True, 20)
