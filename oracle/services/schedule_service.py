"""Ticks on a timer inside the GUI server: weekdays at one local time. The scheduler polls
rather than sleeping until the due time, so a changed setting or a woken laptop is noticed
within a poll."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from oracle.services.context import AppContext
from oracle.services.activity_service import JobRunner, submit_tick
from oracle.services.settings_service import reload_live_settings
from oracle.services.tick_service import TickRun, last_tick, record_skipped, ticked_since
from oracle.store.watchlists import WatchlistRepo

__all__ = ["ScheduleView", "Scheduler", "last_due", "next_due", "schedule_status"]

log = logging.getLogger(__name__)

POLL_S = 30.0


def _at(day: date, hhmm: str, tz: tzinfo | None) -> datetime:
    wall = datetime.combine(day, time.fromisoformat(hhmm))
    # No tz: the machine's own zone, which a naive astimezone() reads from the OS.
    return (wall.replace(tzinfo=tz) if tz else wall.astimezone()).astimezone(timezone.utc)


def _local_day(now: datetime, tz: tzinfo | None) -> date:
    return now.astimezone(tz).date()


def last_due(now: datetime, hhmm: str, tz: tzinfo | None = None) -> datetime:
    """The most recent weekday run time at or before `now`, in UTC."""
    today = _local_day(now, tz)
    days = (today - timedelta(n) for n in range(8))
    return next(t for d in days if d.weekday() < 5 and (t := _at(d, hhmm, tz)) <= now)


def next_due(now: datetime, hhmm: str, tz: tzinfo | None = None) -> datetime:
    """The first weekday run time after `now`, in UTC."""
    today = _local_day(now, tz)
    days = (today + timedelta(n) for n in range(8))
    return next(t for d in days if d.weekday() < 5 and (t := _at(d, hhmm, tz)) > now)


@dataclass(frozen=True)
class ScheduleView:
    enabled: bool
    time: str
    next_run: datetime | None
    """None when scheduling is off."""
    watched: int
    """Listings on any watchlist: with none, a tick has nothing to do."""


def schedule_status(ctx: AppContext, tz: tzinfo | None = None) -> ScheduleView:
    s = ctx.config.schedule
    next_run = next_due(ctx.clock.utcnow(), s.time, tz) if s.enabled else None
    return ScheduleView(s.enabled, s.time, next_run, len(WatchlistRepo(ctx.conn).all_symbols()))


class Scheduler:
    def __init__(self, ctx: AppContext, runner: JobRunner, tz: tzinfo | None = None) -> None:
        """A due tick runs as a job on `runner`, so Activity lists it and can cancel it."""
        self._ctx = ctx
        self._tz = tz
        self._runner = runner
        self._setting = self._live_setting()
        self._started = self._polled = ctx.clock.utcnow()
        self._file_stamp = self._stamp()
        # Due times before the floor never fire, so turning scheduling on or moving its time
        # never runs at once for a time already passed. At start there is none: the run
        # missed while the app was closed (only the latest) runs once, as a catch-up.
        self._floor = datetime.min.replace(tzinfo=timezone.utc)

    async def check(self) -> TickRun | None:
        """One poll: run a tick if one is due and none has run since."""
        if (stamp := self._stamp()) != self._file_stamp:
            self._file_stamp = stamp
            reload_live_settings(self._ctx)
        ctx, s = self._ctx, self._ctx.config.schedule
        now = ctx.clock.utcnow()
        if (setting := self._live_setting()) != self._setting:
            # Changed some time after the last poll: a run due since then still counts.
            self._setting, self._floor = setting, self._polled
        self._polled = now
        if not (s.enabled and ctx.config.automation.enabled):
            return None
        due = last_due(now, s.time, self._tz)
        # A tick finishing after the due time covers it, even one started just before.
        if due < self._floor or ticked_since(ctx, due):
            return None
        trigger = "catch-up" if due < self._started else "scheduled"
        if trigger == "catch-up" and last_tick(ctx) is None:
            return None  # never ticked, so nothing was missed
        if self._runner.active("tick"):
            return record_skipped(ctx, trigger, "skipped: a tick is already queued or running here")
        job = await self._runner.wait(submit_tick(ctx, self._runner, trigger, skip_if_busy=True).id)
        if job.state == "done":
            return job.result
        # A tick cut off part way stored its own record. One cancelled before it started did
        # not, and without one this due time would be submitted again on the next poll.
        return last_tick(ctx) if ticked_since(ctx, due) else record_skipped(ctx, trigger, f"skipped: {job.error}")

    def _live_setting(self) -> tuple:
        return self._ctx.config.schedule, self._ctx.config.automation

    def _stamp(self) -> int | None:
        path = self._ctx.config_source
        try:
            return path.stat().st_mtime_ns if path else None
        except OSError:
            return None

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                # A bug in one poll must not end scheduling for the life of the server.
                log.exception("scheduled tick failed")
            await self._ctx.clock.sleep(POLL_S)
