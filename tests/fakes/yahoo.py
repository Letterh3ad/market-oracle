"""A fake Yahoo chart endpoint shaped like the real one (captured 2026-09-22)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import httpx


@dataclass
class Series:
    currency: str
    tz: str
    bars: list[tuple]
    """(date, open, high, low, close, adj_close, volume); None fields allowed."""
    open_at: time = time(9, 30)


class FakeYahoo:
    hosts = {"query1.finance.yahoo.com", "query2.finance.yahoo.com"}

    def __init__(self) -> None:
        self.series: dict[str, Series] = {}
        self.fail: dict[str, int] = {}
        """symbol -> HTTP status to answer with instead."""

    def add(self, symbol: str, currency: str, tz: str, bars: list[tuple], open_at: time = time(9, 30)) -> None:
        self.series[symbol] = Series(currency, tz, bars, open_at)

    def handle(self, request: httpx.Request) -> httpx.Response:
        prefix = "/v8/finance/chart/"
        if not request.url.path.startswith(prefix):
            return httpx.Response(404)
        symbol = request.url.path.removeprefix(prefix)
        if symbol in self.fail:
            return httpx.Response(self.fail[symbol])
        s = self.series.get(symbol)
        if s is None:
            return httpx.Response(404, json={"chart": {"result": None, "error": {
                "code": "Not Found", "description": "No data found, symbol may be delisted"}}})
        stamps = [(self._stamp(b[0], s), b) for b in s.bars]
        p1 = request.url.params.get("period1")
        if p1 is not None:
            p2 = int(request.url.params.get("period2", 2**40))
            stamps = [(t, b) for t, b in stamps if int(p1) <= t < p2]
        return httpx.Response(200, json={"chart": {"error": None, "result": [{
            "meta": {"currency": s.currency, "symbol": symbol, "exchangeTimezoneName": s.tz,
                     "instrumentType": "EQUITY"},
            "timestamp": [t for t, _ in stamps],
            "indicators": {
                "quote": [{
                    "open": [b[1] for _, b in stamps], "high": [b[2] for _, b in stamps],
                    "low": [b[3] for _, b in stamps], "close": [b[4] for _, b in stamps],
                    "volume": [b[6] for _, b in stamps],
                }],
                "adjclose": [{"adjclose": [b[5] for _, b in stamps]}],
            },
        }]}})

    @staticmethod
    def _stamp(day: date, s: Series) -> int:
        local = datetime.combine(day, s.open_at, tzinfo=ZoneInfo(s.tz))
        return int(local.astimezone(timezone.utc).timestamp())


def bar(day: str, close: float, adj: float | None = None, volume: int = 1000) -> tuple:
    d = date.fromisoformat(day)
    return (d, close, close, close, close, close if adj is None else adj, volume)
