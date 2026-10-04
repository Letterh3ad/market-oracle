"""The compute window (steer.md goal 4, research-lab spec 14): heavy work such as bulk downloads
runs only weekdays 01:00-18:00 and weekends 01:00-08:00, local time."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from oracle.services.context import AppContext

__all__ = ["WINDOW_TEXT", "local_now", "next_open", "window_open"]

OPENS = time(1)
CLOSES = {False: time(18), True: time(8)}
"""Closing time by weekend."""
WINDOW_TEXT = "the compute window (weekdays 01:00-18:00, weekends 01:00-08:00, local time)"


def local_now(ctx: AppContext) -> datetime:
    return ctx.clock.utcnow().astimezone()


def window_open(local: datetime) -> bool:
    return OPENS <= local.time() < CLOSES[local.weekday() >= 5]


def next_open(local: datetime) -> datetime:
    """`local` itself when the window is open; else the next 01:00."""
    if window_open(local):
        return local
    day = local.date() if local.time() < OPENS else local.date() + timedelta(1)
    return datetime.combine(day, OPENS, local.tzinfo)
