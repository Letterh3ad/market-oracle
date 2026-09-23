"""Ticks on a timer inside the GUI server: weekdays at one local time. The scheduler polls
rather than sleeping until the due time, so a changed setting or a woken laptop is noticed
within a poll."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from oracle.services.context import AppContext
from oracle.services.errors import JobAlreadyRunning
from oracle.services.tick_service import TickRun, last_tick, record_skipped, run_tick
from oracle.store.watchlists import WatchlistRepo

__all__ = ["ScheduleView", "Scheduler", "last_due", "next_due", "schedule_status"]

log = logging.getLogger(__name__)

POLL_S = 30.0


def _at(day: date, hhmm: str, tz: tzinfo | None) -> datetime:
    wall = datetime.combine(day, time.fromisoformat(hhmm))
    # No tz: the machine's own zone, which a naive astimezone() reads from the OS.
    return (wall.replace(tzinfo=tz) if tz else wall.astimezone()).astimezone(timezone.utc)


def _local_day(now: datetime, tz: tzinfo | None) -> date:
    return (now.astimezone(tz) if tz else now.astimezone()).date()


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
    def __init__(self, ctx: AppContext, tz: tzinfo | None = None) -> None:
        self._ctx = ctx
        self._tz = tz
        self._setting = ctx.config.schedule
        # Only due times from here on count, so turning scheduling on or moving its time
        # never fires at once for a time already passed.
        self._floor = self._polled = ctx.clock.utcnow()

    async def check(self) -> TickRun | None:
        """One poll: run a tick if one is due and none has run since."""
        ctx, s = self._ctx, self._ctx.config.schedule
        now = ctx.clock.utcnow()
        if s != self._setting:
            # Changed some time after the last poll: a run due since then still counts.
            self._setting, self._floor = s, self._polled
        self._polled = now
        if not s.enabled:
            return None
        due = last_due(now, s.time, self._tz)
        last = last_tick(ctx)
        if due < self._floor or (last is not None and last.started_at >= due):
            return None
        try:
            return await run_tick(ctx, "scheduled")
        except JobAlreadyRunning as exc:
            return record_skipped(ctx, "scheduled", f"skipped: {exc}")

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                # A bug in one poll must not end scheduling for the life of the server.
                log.exception("scheduled tick failed")
            await self._ctx.clock.sleep(POLL_S)
