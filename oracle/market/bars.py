from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Quotes in a currency's minor unit: London in pence, Johannesburg in cents, Tel Aviv
# in agorot. Stored in the major unit so every price compares directly.
_SUB_UNITS = {
    "GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ZAC": ("ZAR", 100), "ILA": ("ILS", 100),
}


@dataclass(frozen=True)
class Bar:
    symbol: str
    date: date
    """The exchange's local trading date."""
    open: float | None
    high: float | None
    low: float | None
    close: float
    adj_close: float | None
    volume: int | None
    currency: str
    source: str


def major_unit(currency: str) -> tuple[str, int]:
    """(major currency, how many quoted units make one)."""
    return _SUB_UNITS.get(currency, (currency, 1))


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def local_date(epoch_s: float, tz_name: str) -> date:
    # Not fromtimestamp: on Windows it raises OSError for a time before 1970 (a first trade day in 1962).
    return (EPOCH + timedelta(seconds=epoch_s)).astimezone(ZoneInfo(tz_name)).date()


# Weekdays a last bar may trail today before it counts as stale: today's bar may not
# exist yet, and one weekday may be a holiday. Holiday calendars are not modelled, so
# a longer closure (Golden Week) briefly shows stale.
STALE_AFTER_WEEKDAYS = 2


def is_stale(last: date, today: date) -> bool:
    weekdays = sum(1 for n in range(1, (today - last).days + 1) if (last + timedelta(days=n)).weekday() < 5)
    return weekdays > STALE_AFTER_WEEKDAYS
