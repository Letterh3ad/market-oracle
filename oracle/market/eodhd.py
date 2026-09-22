"""EODHD (eodhd.com, paid): daily bars, FX and fundamentals.

Only US symbols are verified against the public demo key; the other exchanges follow
EODHD's documented codes and are checked by the keyed network smoke test."""

from __future__ import annotations

from datetime import date, datetime

from oracle.market import sources
from oracle.market.bars import Bar, major_unit
from oracle.market.sources import Instrument, NoData, SourceFailed
from oracle.net.client import RateLimitedClient

SOURCE = "eodhd"
API = "https://eodhd.com/api"

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


class EodhdClient:
    """Authenticated JSON calls; the key rides in the query string, as EODHD requires."""

    def __init__(self, client: RateLimitedClient, key: str) -> None:
        self._client = client
        self._key = key

    async def get_json(self, path: str, params: dict | None = None):
        try:
            resp = await sources.get(
                self._client, SOURCE, f"{API}/{path}",
                {"api_token": self._key, "fmt": "json", **(params or {})},
                not_found=(), secret=self._key,
            )
        except SourceFailed as exc:
            if str(exc) in ("HTTP 401", "HTTP 403"):
                raise SourceFailed("key rejected") from None
            if str(exc) == "HTTP 404":
                raise NoData(f"{path.rsplit('/', 1)[-1]} not found") from None
            raise
        return resp.json()

    async def eod(self, code: str, since: date | None, now: datetime) -> list[dict]:
        """Raw daily rows for an EODHD code (`AAPL.US`, `GBPUSD.FOREX`)."""
        start = since if since is not None else now.date() - sources.HISTORY
        rows = await self.get_json(f"eod/{code}", {"from": start.isoformat()})
        if not isinstance(rows, list):
            raise SourceFailed("unreadable response")
        return rows


class EodhdPrices:
    name = SOURCE

    def __init__(self, eodhd: EodhdClient) -> None:
        self._eodhd = eodhd

    async def daily_bars(self, instrument: Instrument, since: date | None, now: datetime) -> list[Bar]:
        code = eodhd_code(instrument.symbol)
        if code is None:
            raise NoData("exchange not covered")
        if not instrument.currency:
            raise NoData("currency unknown")
        rows = await self._eodhd.eod(code, since, now)
        quoted = MINOR_UNITS.get((code.rpartition(".")[2], instrument.currency), instrument.currency)
        currency, per_unit = major_unit(quoted)
        return [_bar(instrument.symbol, row, currency, per_unit) for row in rows]


def _bar(symbol: str, row: dict, currency: str, per_unit: int) -> Bar:
    def scaled(v):
        return None if v is None else float(v) / per_unit

    return Bar(
        symbol=symbol, date=date.fromisoformat(row["date"]),
        open=scaled(row.get("open")), high=scaled(row.get("high")), low=scaled(row.get("low")),
        close=float(row["close"]) / per_unit, adj_close=scaled(row.get("adjusted_close")),
        volume=row.get("volume"), currency=currency, source=SOURCE,
    )
