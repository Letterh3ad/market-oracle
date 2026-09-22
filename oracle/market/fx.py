"""Daily exchange rates as units of a currency per one unit of the base currency."""

from __future__ import annotations

import bisect
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from oracle.market import sources, yahoo
from oracle.market.eodhd import EodhdClient
from oracle.market.sources import Instrument, NoData
from oracle.net.client import RateLimitedClient

# How far back a rate may stand in for a day without one (weekends, holidays).
MAX_RATE_AGE = timedelta(days=5)
# What the ECB publishes (and Frankfurter, which serves ECB data), plus the euro.
ECB_CURRENCIES = frozenset({
    "AUD", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP", "HKD", "HUF", "IDR", "ILS",
    "INR", "ISK", "JPY", "KRW", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "RON", "SEK", "SGD",
    "THB", "TRY", "USD", "ZAR",
})


@dataclass(frozen=True)
class FxRate:
    currency: str
    date: date
    units_per_base: float
    source: str


class FxSource(Protocol):
    name: str

    async def daily_rates(self, base: str, currency: str, since: date | None, now: datetime) -> list[FxRate]: ...


def pick_rate(rates: list[FxRate], day: date) -> FxRate | None:
    """The rate for `day`, or the last one before it within MAX_RATE_AGE. `rates` is
    sorted by date."""
    i = bisect.bisect_right([r.date for r in rates], day)
    if i == 0:
        return None
    rate = rates[i - 1]
    return rate if day - rate.date <= MAX_RATE_AGE else None


class YahooFx:
    """`GBPUSD=X` is a daily series like any listing's: its close is USD per GBP."""

    name = "yahoo"

    def __init__(self, client: RateLimitedClient) -> None:
        self._prices = yahoo.YahooPrices(client)

    async def daily_rates(self, base, currency, since, now):
        bars = await self._prices.daily_bars(Instrument(f"{base}{currency}=X", None), since, now)
        return [FxRate(currency, b.date, b.close, self.name) for b in bars]


class EodhdFx:
    name = "eodhd"

    def __init__(self, eodhd: EodhdClient) -> None:
        self._eodhd = eodhd

    async def daily_rates(self, base, currency, since, now):
        rows = await self._eodhd.eod(f"{base}{currency}.FOREX", since, now)
        return [FxRate(currency, date.fromisoformat(r["date"]), float(r["close"]), self.name) for r in rows]


class EcbFx:
    """Official ECB reference rates, per euro; other bases go through the euro."""

    name = "ecb"
    URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.{currencies}.EUR.SP00.A"

    def __init__(self, client: RateLimitedClient) -> None:
        self._client = client

    async def daily_rates(self, base, currency, since, now):
        if not {base, currency} <= ECB_CURRENCIES:
            raise NoData("not an ECB currency")
        wanted = sorted({base, currency} - {"EUR"})
        start = since if since is not None else now.date() - sources.HISTORY
        resp = await sources.get(self._client, self.name, self.URL.format(currencies="+".join(wanted)),
                                 {"startPeriod": start.isoformat(), "format": "csvdata"}, not_found=(404, 422))
        per_eur: dict[str, dict[date, float]] = {}
        for row in csv.DictReader(io.StringIO(resp.text)):
            if row.get("OBS_VALUE"):
                per_eur.setdefault(row["CURRENCY"], {})[date.fromisoformat(row["TIME_PERIOD"])] = float(row["OBS_VALUE"])

        def per_euro(code: str, day: date) -> float | None:
            return 1.0 if code == "EUR" else per_eur.get(code, {}).get(day)

        days = sorted(set().union(*per_eur.values())) if per_eur else []
        out = []
        for day in days:
            cur, bas = per_euro(currency, day), per_euro(base, day)
            if cur is not None and bas is not None:
                out.append(FxRate(currency, day, cur / bas, self.name))
        return out


class FrankfurterFx:
    """ECB data from a second host, with the base conversion done server-side."""

    name = "frankfurter"
    URL = "https://api.frankfurter.app/{start}.."

    def __init__(self, client: RateLimitedClient) -> None:
        self._client = client

    async def daily_rates(self, base, currency, since, now):
        if not {base, currency} <= ECB_CURRENCIES:
            raise NoData("not an ECB currency")
        start = since if since is not None else now.date() - sources.HISTORY
        resp = await sources.get(self._client, self.name, self.URL.format(start=start.isoformat()),
                                 {"from": base, "to": currency}, not_found=(404, 422))
        rates = resp.json()["rates"]
        return sorted(
            (FxRate(currency, date.fromisoformat(d), float(v[currency]), self.name) for d, v in rates.items()),
            key=lambda r: r.date,
        )


# Required by its free tier wherever its rates are displayed.
OPEN_ER_ATTRIBUTION = "Rates By Exchange Rate API (https://www.exchangerate-api.com)"


class OpenErFx:
    """Today's rate only, ~160 currencies. One call answers every currency, so it is
    made once per instance (one update run)."""

    name = "open_er_api"
    URL = "https://open.er-api.com/v6/latest/{base}"

    def __init__(self, client: RateLimitedClient) -> None:
        self._client = client
        self._latest: dict[str, dict] = {}

    async def daily_rates(self, base, currency, since, now):
        if base not in self._latest:
            resp = await sources.get(self._client, self.name, self.URL.format(base=base))
            self._latest[base] = resp.json()
        body = self._latest[base]
        value = (body.get("rates") or {}).get(currency)
        if value is None:
            raise NoData("currency not covered")
        day = datetime.fromtimestamp(body["time_last_update_unix"], timezone.utc).date()
        return [FxRate(currency, day, float(value), self.name)]
