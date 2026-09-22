from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

# Quotes in a currency's minor unit: London in pence, Johannesburg in cents, Tel Aviv
# in agorot. Stored in the major unit so every price compares directly.
_SUB_UNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}


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


def local_date(epoch_s: float, tz_name: str) -> date:
    return datetime.fromtimestamp(epoch_s, timezone.utc).astimezone(ZoneInfo(tz_name)).date()
