"""Fakes for the free FX services, shaped like their real responses (2026-09-22)."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

import httpx


class FakeEcb:
    """ECB SDMX: `EXR/D.USD+GBP.EUR.SP00.A` rows of CURRENCY per EUR, as csvdata."""

    hosts = {"data-api.ecb.europa.eu"}

    def __init__(self) -> None:
        self.per_eur: dict[str, dict[str, float]] = {}
        """currency -> {iso date: units per EUR}"""
        self.fail: int | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(self.fail)
        prefix = "/service/data/EXR/D."
        path = request.url.path
        if not path.startswith(prefix):
            return httpx.Response(404)
        wanted = path.removeprefix(prefix).split(".")[0].split("+")
        start = request.url.params.get("startPeriod", "0000")
        lines = ["KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS"]
        for cur in wanted:
            for day, value in sorted(self.per_eur.get(cur, {}).items()):
                if day >= start:
                    lines.append(f"EXR.D.{cur}.EUR.SP00.A,D,{cur},EUR,SP00,A,{day},{value},A")
        if len(lines) == 1:
            return httpx.Response(404, text="No results found.")
        return httpx.Response(200, text="\n".join(lines) + "\n", headers={"content-type": "text/csv"})


class FakeFrankfurter:
    """`/{start}..?from=GBP&to=USD` -> {"rates": {date: {USD: v}}}, ECB currencies only."""

    hosts = {"api.frankfurter.app"}

    def __init__(self) -> None:
        self.per_base: dict[str, dict[str, float]] = {}
        """currency -> {iso date: units per GBP}"""
        self.fail: int | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(self.fail)
        start = request.url.path.strip("/").removesuffix("..")
        cur = request.url.params["to"]
        series = self.per_base.get(cur)
        if series is None:
            return httpx.Response(404, json={"message": "not found"})
        rates = {d: {cur: v} for d, v in sorted(series.items()) if d >= start}
        return httpx.Response(200, json={"amount": 1.0, "base": request.url.params["from"], "rates": rates})


class FakeOpenEr:
    """`/v6/latest/GBP` -> today's rates for ~160 currencies, no history."""

    hosts = {"open.er-api.com"}

    def __init__(self, updated: datetime) -> None:
        self.updated = updated
        self.rates: dict[str, float] = {}
        self.calls = 0
        self.garbage = False
        """Answer 200 with an HTML page, as a captive portal or outage page would."""

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.garbage:
            return httpx.Response(200, text="<html>maintenance</html>")
        return httpx.Response(200, json={
            "result": "success", "base_code": request.url.path.rsplit("/", 1)[1],
            "time_last_update_unix": int(self.updated.timestamp()),
            "rates": {"GBP": 1, **self.rates},
        })


def noon(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), time(12), tzinfo=timezone.utc)
