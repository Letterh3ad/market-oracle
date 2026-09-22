"""EODHD (eodhd.com, paid): daily bars for listings on the exchanges mapped below.

Only US symbols are verified against the public demo key; the others follow EODHD's
documented exchange codes and are checked by the keyed network smoke test."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from oracle.market.bars import Bar, major_unit
from oracle.market.sources import Instrument, NoData, SourceFailed
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed

SOURCE = "eodhd"
EOD_URL = "https://eodhd.com/api/eod/{code}"
HISTORY = timedelta(days=5 * 365 + 1)

# Yahoo suffix -> EODHD exchange code. No suffix is a US listing.
EXCHANGES = {
    "": "US", "L": "LSE", "DE": "XETRA", "F": "F", "PA": "PA", "AS": "AS", "MI": "MI",
    "MC": "MC", "SW": "SW", "TO": "TO", "V": "V", "T": "TSE", "HK": "HK", "KS": "KO",
    "KQ": "KQ", "NS": "NSE", "BO": "BSE", "AX": "AU",
}
# EODHD quotes these exchanges' home-currency lines in the minor unit.
MINOR_UNITS = {("LSE", "GBP"): "GBp"}


def eodhd_code(symbol: str) -> str | None:
    base, _, suffix = symbol.rpartition(".") if "." in symbol else (symbol, "", "")
    exchange = EXCHANGES.get(suffix)
    return f"{base}.{exchange}" if exchange else None


class EodhdPrices:
    name = SOURCE

    def __init__(self, client: RateLimitedClient, key: str) -> None:
        self._client = client
        self._key = key

    async def daily_bars(self, instrument: Instrument, since: date | None, now: datetime) -> list[Bar]:
        code = eodhd_code(instrument.symbol)
        if code is None:
            raise NoData("exchange not covered")
        if not instrument.currency:
            raise NoData("currency unknown")
        rows = await self.eod(code, since, now)
        quoted = MINOR_UNITS.get((code.rpartition(".")[2], instrument.currency), instrument.currency)
        currency, per_unit = major_unit(quoted)
        try:
            return [_bar(instrument.symbol, row, currency, per_unit) for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceFailed(f"unreadable response ({type(exc).__name__})") from None

    async def eod(self, code: str, since: date | None, now: datetime) -> list[dict]:
        """Raw daily rows for an EODHD code (`AAPL.US`, `GBPUSD.FOREX`)."""
        start = since if since is not None else now.date() - HISTORY
        params = {"api_token": self._key, "fmt": "json", "from": start.isoformat()}
        try:
            resp = await self._client.get(SOURCE, EOD_URL.format(code=code), params=params)
        except (RequestFailed, CircuitOpen) as exc:
            raise SourceFailed(self._redact(str(exc).removeprefix(f"{SOURCE}: "))) from None
        if resp.status_code in (401, 403):
            raise SourceFailed("key rejected")
        if resp.status_code == 404:
            raise NoData(f"{code} not found")
        if resp.status_code != 200:
            raise SourceFailed(f"HTTP {resp.status_code}")
        try:
            rows = resp.json()
        except ValueError:
            raise SourceFailed("unreadable response") from None
        if not isinstance(rows, list):
            raise SourceFailed("unreadable response")
        return rows

    def _redact(self, text: str) -> str:
        # The key travels in the query string, so any message quoting a URL carries it.
        return text.replace(self._key, "***")


def _bar(symbol: str, row: dict, currency: str, per_unit: int) -> Bar:
    def scaled(v):
        return None if v is None else float(v) / per_unit

    return Bar(
        symbol=symbol, date=date.fromisoformat(row["date"]),
        open=scaled(row.get("open")), high=scaled(row.get("high")), low=scaled(row.get("low")),
        close=float(row["close"]) / per_unit, adj_close=scaled(row.get("adjusted_close")),
        volume=row.get("volume"), currency=currency, source=SOURCE,
    )
