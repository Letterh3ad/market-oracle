from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from urllib.parse import quote

from oracle.market import sources
from oracle.market.bars import Bar, local_date, major_unit
from oracle.market.sources import Instrument, NoData, SourceFailed
from oracle.net.client import RateLimitedClient

SOURCE = "yahoo"
CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
# Yahoo answers its crumb endpoint with 429 to non-browser user agents (checked
# 2026-09-22), so the crumb flow presents as a browser, as yfinance does.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36"
)


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


class QuoteSummary:
    """quoteSummary needs a session cookie (from fc.yahoo.com) and a crumb bound to it.
    The client keeps cookies, so one instance serves one session; a rejected crumb is
    renewed once."""

    def __init__(self, client: RateLimitedClient) -> None:
        self._client = client
        self._crumb: str | None = None

    async def modules(self, symbol: str, modules: str) -> dict:
        """The named modules (comma-separated) of `symbol`; NoData when Yahoo has no such symbol."""
        url = SUMMARY_URL.format(symbol=quote(symbol, safe=""))
        for _ in range(2):
            if self._crumb is None:
                self._crumb = await self._new_crumb()
            try:
                resp = await self._get(url, {"modules": modules, "crumb": self._crumb})
            except SourceFailed as exc:
                if exc.status != 401:
                    raise
                self._crumb = None
                continue
            return resp.json()["quoteSummary"]["result"][0]
        raise SourceFailed("crumb rejected twice")

    async def _new_crumb(self) -> str:
        try:
            await self._get("https://fc.yahoo.com")
        except NoData:
            pass
        except SourceFailed as exc:
            # A 4xx still sets the cookie (404 is the usual answer and lands above).
            if exc.status is None or exc.status == 429 or exc.status >= 500:
                raise SourceFailed(f"no crumb ({exc})") from None
        try:
            resp = await self._get("https://query2.finance.yahoo.com/v1/test/getcrumb")
        except (NoData, SourceFailed) as exc:
            raise SourceFailed(f"no crumb ({exc})") from None
        if not resp.text.strip():
            raise SourceFailed("no crumb (empty answer)")
        return resp.text.strip()

    async def _get(self, url: str, params: dict | None = None):
        return await sources.get(self._client, SOURCE, url, params, headers={"User-Agent": BROWSER_UA})


PROFILE_MODULES = "price,assetProfile"


@dataclass(frozen=True)
class YahooProfile:
    """What Yahoo says a listing is, for adding one the universe lacks."""

    symbol: str
    name: str | None
    quote_type: str | None
    """EQUITY for a company's shares; ETF, MUTUALFUND and others are not."""
    exchange: str | None
    """Yahoo's code, the one FinanceDatabase uses (KSC, LSE)."""
    currency: str | None
    """As quoted: GBp for a pence line."""
    market_cap: float | None
    """In the major unit of `currency`."""
    country: str | None
    sector: str | None
    """Yahoo's own sectors (Technology, Consumer Cyclical), not the universe's."""
    industry: str | None
    website: str | None
    summary: str | None


def parse_profile(symbol: str, modules: dict) -> YahooProfile:
    price, profile = modules.get("price") or {}, modules.get("assetProfile") or {}
    cap = price.get("marketCap")
    cap = cap.get("raw") if isinstance(cap, dict) else cap

    def text(module: dict, key: str) -> str | None:
        value = module.get(key)
        return value.strip() or None if isinstance(value, str) else None

    return YahooProfile(
        symbol=symbol, name=text(price, "longName") or text(price, "shortName"),
        quote_type=text(price, "quoteType"), exchange=text(price, "exchange"), currency=text(price, "currency"),
        market_cap=float(cap) if isinstance(cap, (int, float)) and cap > 0 else None,
        country=text(profile, "country"), sector=text(profile, "sector"), industry=text(profile, "industry"),
        website=text(profile, "website"), summary=text(profile, "longBusinessSummary"),
    )


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
