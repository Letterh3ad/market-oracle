from __future__ import annotations

from datetime import date, datetime, time, timezone
from urllib.parse import quote

from oracle.market.bars import Bar, local_date, major_unit
from oracle.market.sources import Instrument, NoData, SourceFailed
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed

SOURCE = "yahoo"
CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
HISTORY = "5y"


class YahooPrices:
    """Daily bars from Yahoo's chart endpoint, the one Yahoo endpoint needing no crumb."""

    name = SOURCE

    def __init__(self, client: RateLimitedClient) -> None:
        self._client = client

    async def daily_bars(self, instrument: Instrument, since: date | None, now: datetime) -> list[Bar]:
        symbol = instrument.symbol
        params = {"interval": "1d", "includeAdjustedClose": "true"}
        if since is None:
            params["range"] = HISTORY
        else:
            params["period1"] = str(_epoch(since))
            params["period2"] = str(int(now.timestamp()))
        url = CHART_URL.format(symbol=quote(symbol, safe=""))
        try:
            resp = await self._client.get(SOURCE, url, params=params)
        except (RequestFailed, CircuitOpen) as exc:
            raise SourceFailed(str(exc).removeprefix(f"{SOURCE}: ")) from exc
        if resp.status_code == 404:
            raise NoData(_error_text(resp) or "no data")
        if resp.status_code != 200:
            raise SourceFailed(f"HTTP {resp.status_code}")
        try:
            return _parse(symbol, resp.json())
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise SourceFailed(f"unreadable chart response ({type(exc).__name__}: {exc})") from None


def _parse(symbol: str, body: dict) -> list[Bar]:
    results = body["chart"]["result"]
    if not results:
        raise NoData("no data")
    r = results[0]
    currency, per_unit = major_unit(r["meta"]["currency"])
    tz = r["meta"].get("exchangeTimezoneName") or "UTC"
    stamps = r.get("timestamp") or []
    quote_ = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose") or [None] * len(stamps)

    def scaled(v):
        return None if v is None else v / per_unit

    bars = []
    for i, ts in enumerate(stamps):
        close = quote_["close"][i]
        if close is None:  # a day Yahoo lists without a trade
            continue
        bars.append(Bar(
            symbol=symbol, date=local_date(ts, tz),
            open=scaled(quote_["open"][i]), high=scaled(quote_["high"][i]), low=scaled(quote_["low"][i]),
            close=close / per_unit, adj_close=scaled(adj[i]), volume=quote_["volume"][i],
            currency=currency, source=SOURCE,
        ))
    return bars


def _epoch(day: date) -> int:
    return int(datetime.combine(day, time(0), tzinfo=timezone.utc).timestamp())


def _error_text(resp) -> str | None:
    try:
        return resp.json()["chart"]["error"]["description"]
    except (ValueError, KeyError, TypeError):
        return None
