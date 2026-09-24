"""Ticks on a timer inside the GUI server: weekdays at one local time, plus the midday pass
at a second. The scheduler polls rather than sleeping until the due time, so a changed
setting or a woken laptop is noticed within a poll."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from oracle.config import AutomationConfig

from oracle.services.context import AppContext
from oracle.services.activity_service import JobRunner, submit_tick
from oracle.services.errors import InvalidQuery
from oracle.services.settings_service import reload_live_settings
from oracle.services.tick_service import (
    KIND_LABELS, MIDDAY, TICK, TickRun, last_tick, record_skipped, ticked_since,
)
from oracle.store.watchlists import WatchlistRepo

__all__ = ["MIDDAY_GRACE", "POSTPONE", "ScheduleView", "Scheduler", "Upcoming", "last_due", "next_due",
           "schedule_status"]

log = logging.getLogger(__name__)

POLL_S = 30.0
POSTPONE = timedelta(hours=1)
MIDDAY_GRACE = timedelta(minutes=30)
"""A midday pass not started this soon after its time is missed, not fired late."""
SKIPPED_BY_USER = "skipped: skipped by the user"


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
    warn_minutes: int
    research_when_idle: bool
    idle_minutes: int
    midday_time: str
    """"" when the midday pass is off."""
    midday_budget_min: int
    midday_next: datetime | None
    """None when scheduling or the midday pass is off."""


def schedule_status(ctx: AppContext, tz: tzinfo | None = None) -> ScheduleView:
    s = ctx.config.schedule
    now = ctx.clock.utcnow()
    next_run = next_due(now, s.time, tz) if s.enabled else None
    midday_next = next_due(now, s.midday_time, tz) if s.enabled and s.midday_time else None
    return ScheduleView(s.enabled, s.time, next_run, len(WatchlistRepo(ctx.conn).all_symbols()), s.warn_minutes,
                        s.research_when_idle, s.idle_minutes, s.midday_time, s.midday_budget_min, midday_next)


@dataclass(frozen=True)
class Upcoming:
    due: datetime
    """The scheduled time, which identifies the tick."""
    at: datetime
    """When it will fire: `due`, or an hour later once postponed."""
    kind: str = TICK

    @property
    def postponed(self) -> bool:
        return self.at != self.due


class Scheduler:
    def __init__(self, ctx: AppContext, runner: JobRunner, tz: tzinfo | None = None) -> None:
        """A due tick runs as a job on `runner`, so Activity lists it and can cancel it."""
        self._ctx = ctx
        self._tz = tz
        self._runner = runner
        self._settings = self._live_settings()
        self._started = self._polled = ctx.clock.utcnow()
        self._file_stamp = self._stamp()
        # Due times before a kind's floor never fire, so turning scheduling on or moving its
        # time never runs at once for a time already passed. At start there is none: the tick
        # missed while the app was closed (only the latest) runs once, as a catch-up. One
        # floor per kind, so changing the midday pass never drops a postponed tick.
        self._floors = dict.fromkeys(KIND_LABELS, datetime.min.replace(tzinfo=timezone.utc))
        # Held here only: a restart forgets a postponement (the tick then fires as a catch-up).
        self._postponed: set[tuple[str, datetime]] = set()

    async def check(self) -> TickRun | None:
        """One poll: run the first slot (tick, then midday pass) that is due and has not run since."""
        if (stamp := self._stamp()) != self._file_stamp:
            self._file_stamp = stamp
            reload_live_settings(self._ctx)
        ctx, s = self._ctx, self._ctx.config.schedule
        now = ctx.clock.utcnow()
        settings = self._live_settings()
        for kind, setting in settings.items():
            if setting != self._settings[kind]:
                # Changed some time after the last poll: a run due since then still counts.
                self._floors[kind] = self._polled
        self._settings, self._polled = settings, now
        if not (s.enabled and ctx.config.automation.enabled):
            return None
        for kind, hhmm in self._slots():
            if (run := await self._fire(kind, hhmm, now)) is not None:
                return run
        return None

    async def _fire(self, kind: str, hhmm: str, now: datetime) -> TickRun | None:
        ctx = self._ctx
        due = last_due(now, hhmm, self._tz)
        # A run finishing after the due time covers it, even one started just before.
        if due < self._floors[kind] or ticked_since(ctx, due, kind):
            return None
        fire_at = self._fire_at(kind, due)
        if now < fire_at:
            return None
        trigger = "catch-up" if due < self._started else "scheduled"
        if kind == MIDDAY:
            if self._missed(fire_at, now):
                if due < self._started:
                    return None  # the app was closed at its time: nothing to say
                # Never fired late, but recorded, so the Ticks panel says why there was none.
                grace = MIDDAY_GRACE // timedelta(minutes=1)
                return record_skipped(ctx, trigger, f"missed: not started within {grace} minutes of its time",
                                      kind=MIDDAY)
        elif trigger == "catch-up" and last_tick(ctx) is None:
            return None  # never ticked, so nothing was missed
        label = KIND_LABELS[kind].lower()
        # Only the same kind: a tick due while a midday pass is queued waits behind it.
        if self._in_flight(kind):
            return record_skipped(ctx, trigger, f"skipped: a {label} is already queued or running here", kind=kind)
        # A midday pass queued behind a long manual tick checks its grace again as it starts.
        deadline = fire_at + MIDDAY_GRACE if kind == MIDDAY else None
        job = await self._runner.wait(
            submit_tick(ctx, self._runner, trigger, skip_if_busy=True, kind=kind, due=due, deadline=deadline).id)
        if job.state == "done":
            return job.result
        # A run cut off part way stored its own record. One cancelled before it started did
        # not, and without one this due time would be submitted again on the next poll.
        if ticked_since(ctx, due, kind):
            return last_tick(ctx, kind)
        return record_skipped(ctx, trigger, f"skipped: {job.error}", kind=kind)

    @staticmethod
    def _missed(fire_at: datetime, now: datetime) -> bool:
        return now - fire_at > MIDDAY_GRACE

    def _in_flight(self, kind: str) -> bool:
        """A run of `kind` queued or running here, which has no stored record until it ends."""
        return any(j.subject == kind for j in self._runner.active("tick"))

    def _fire_at(self, kind: str, due: datetime) -> datetime:
        return due + POSTPONE if (kind, due) in self._postponed else due

    def _slots(self) -> list[tuple[str, str]]:
        s = self._ctx.config.schedule
        return [(TICK, s.time)] + ([(MIDDAY, s.midday_time)] if s.midday_time else [])

    def upcoming(self) -> Upcoming | None:
        """The next scheduled run (tick or midday pass) not yet run or skipped; None while
        scheduling or automation is off."""
        ctx, s = self._ctx, self._ctx.config.schedule
        if not (s.enabled and ctx.config.automation.enabled):
            return None
        now = ctx.clock.utcnow()
        return min((self._upcoming(kind, hhmm, now) for kind, hhmm in self._slots()), key=lambda u: u.at)

    def _upcoming(self, kind: str, hhmm: str, now: datetime) -> Upcoming:
        ctx = self._ctx
        last = last_due(now, hhmm, self._tz)
        fire_at = self._fire_at(kind, last)
        if last >= self._floors[kind] and not ticked_since(ctx, last, kind) and not self._in_flight(kind):
            # Still to run: a postponed tick, or a midday pass due but not yet submitted
            # (between polls) inside its grace, so the banner acts on it. Once submitted it is
            # past postponing: the banner moves on to the next run.
            if fire_at != last if kind == TICK else not self._missed(fire_at, now):
                return Upcoming(last, fire_at, kind)
        due = next_due(now, hhmm, self._tz)
        while ticked_since(ctx, due, kind):  # skipped ahead of time
            due = next_due(due, hhmm, self._tz)
        return Upcoming(due, due + POSTPONE if (kind, due) in self._postponed else due, kind)

    def warning(self) -> Upcoming | None:
        """The upcoming tick or midday pass once it is within `warn_minutes` of firing."""
        minutes = self._ctx.config.schedule.warn_minutes
        up = self.upcoming()
        if not (minutes and up) or up.at - self._ctx.clock.utcnow() > timedelta(minutes=minutes):
            return None
        return up

    def postpone(self) -> Upcoming:
        """Move the upcoming run an hour later, once per due time."""
        up = self._pending()
        if up.postponed:
            raise InvalidQuery(f"this {KIND_LABELS[up.kind].lower()} was already postponed once; skip it instead")
        self._postponed.add((up.kind, up.due))
        return Upcoming(up.due, up.due + POSTPONE, up.kind)

    def skip(self) -> TickRun:
        """Skip the upcoming run, stored as skipped at its due time, so a restart honours it too."""
        up = self._pending()
        return record_skipped(self._ctx, "scheduled", SKIPPED_BY_USER, at=up.due, kind=up.kind)

    def _pending(self) -> Upcoming:
        if (up := self.upcoming()) is None:
            raise InvalidQuery("no scheduled tick or midday pass: scheduling or automation is off")
        return up

    def _live_settings(self) -> dict[str, tuple[bool, str, AutomationConfig]]:
        s, automation = self._ctx.config.schedule, self._ctx.config.automation
        return {TICK: (s.enabled, s.time, automation), MIDDAY: (s.enabled, s.midday_time, automation)}

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
