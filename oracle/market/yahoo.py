from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from urllib.parse import quote

from oracle.market import sources
from oracle.market.bars import Bar, local_date, major_unit
from oracle.market.sources import Instrument, NoData
from oracle.net.client import RateLimitedClient

SOURCE = "yahoo"
CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"


class YahooPrices:
    """Daily bars from Yahoo's chart endpoint, the one Yahoo endpoint needing no crumb."""

    name = SOURCE

    def __init__(self, client: RateLimitedClient) -> None:
        self._client = client

    async def daily_bars(self, instrument: Instrument, since: date | None, now: datetime) -> list[Bar]:
        params = {"interval": "1d", "includeAdjustedClose": "true"}
        if since is None:
            params["range"] = f"{sources.HISTORY_YEARS}y"
        else:
            params["period1"] = str(_epoch(since))
            params["period2"] = str(int(now.timestamp()))
        url = CHART_URL.format(symbol=quote(instrument.symbol, safe=""))
        resp = await sources.get(self._client, SOURCE, url, params)
        return _parse(instrument.symbol, resp.json())


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


SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
# Its own limiter and breaker: a burst of name searches must never pause prices.
SEARCH_SOURCE = "yahoo_search"


@dataclass(frozen=True)
class Quote:
    """One line Yahoo's search finds for a name."""

    symbol: str
    name: str
    short_name: str
    kind: str
    """Yahoo's quoteType: EQUITY, ETF, MUTUALFUND..."""
    score: float


async def search_quotes(client: RateLimitedClient, text: str) -> list[Quote]:
    """The lines Yahoo's search finds for `text`, best first: NoData or SourceFailed when it
    cannot say."""
    params = {"q": text, "quotesCount": "6", "newsCount": "0", "listsCount": "0"}
    resp = await sources.get(client, SEARCH_SOURCE, SEARCH_URL, params)
    # A consent page, or quotes of another shape, is no answer: never a failure of the caller's run.
    try:
        quotes = resp.json()["quotes"]
        if not isinstance(quotes, list):
            raise TypeError("quotes is not a list")
        return [Quote(q["symbol"], q.get("longname") or q.get("shortname") or q["symbol"],
                      q.get("shortname") or "", q.get("quoteType") or "", float(q.get("score") or 0))
                for q in quotes if isinstance(q, dict) and isinstance(q.get("symbol"), str)]
    except (ValueError, KeyError, TypeError) as exc:
        raise NoData(f"unreadable search answer ({type(exc).__name__})") from None
