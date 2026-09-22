"""Synthetic daily bars for analyst tests: one per weekday, so gaps match real markets."""

from datetime import date, timedelta

from oracle.market.bars import Bar

START = date(2025, 1, 6)  # a Monday


def weekdays(n: int, start: date = START) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def series(closes: list[float], symbol: str = "TEST", currency: str = "USD") -> list[Bar]:
    return [
        Bar(symbol, d, c, c, c, c, c, 1000, currency, "test")
        for d, c in zip(weekdays(len(closes)), closes)
    ]


def geometric(n: int, daily: float, start: float = 100.0) -> list[float]:
    return [start * (1 + daily) ** i for i in range(n)]
