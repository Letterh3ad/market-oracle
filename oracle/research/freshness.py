"""Which listings' research is stale enough to run again."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Known:
    """What is stored about one listing: its newest report's day and the close then and now,
    whether that report has a valid verdict, and when research on it last started."""

    symbol: str
    report_day: date | None
    close_then: float | None
    close_now: float | None
    has_verdict: bool = True
    attempted: datetime | None = None
    """Failed runs included; None when never tried."""

    @property
    def move(self) -> float | None:
        if not self.close_then or self.close_now is None:
            return None
        # Rounded so an exact 5% move is not 5.0000000001%.
        return round(self.close_now / self.close_then - 1, 9)


@dataclass(frozen=True)
class Due:
    symbol: str
    why: str


def due_for_research(known: list[Known], today: date, max_age_days: int, move_threshold: float) -> list[Due]:
    """Listings with no report, one without a valid verdict, one older than `max_age_days`, or a
    price move past `move_threshold` since it. Never tried first, then the least recently tried,
    then the biggest move: ordering by report day would put a listing whose research always
    fails first on every tick, and it could spend the whole budget."""
    due: list[tuple[Known, str]] = []
    for k in known:
        if k.report_day is None:
            due.append((k, "no report yet"))
        elif not k.has_verdict:
            due.append((k, f"the report from {k.report_day.isoformat()} has no verdict"))
        elif (age := (today - k.report_day).days) > max_age_days:
            due.append((k, f"report from {k.report_day.isoformat()}, {age} days old"))
        elif k.move is not None and abs(k.move) > move_threshold:
            due.append((k, f"price moved {k.move:+.1%} since the report"))
    due.sort(key=lambda d: (d[0].attempted is not None, d[0].attempted or datetime.min, -abs(d[0].move or 0)))
    return [Due(k.symbol, why) for k, why in due]
