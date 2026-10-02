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
    kind: str = "EQUITY"
    first_trade: date | None = None


class FakeYahoo:
    hosts = {"query1.finance.yahoo.com", "query2.finance.yahoo.com", "fc.yahoo.com"}

    def __init__(self) -> None:
        self.series: dict[str, Series] = {}
        self.fail: dict[str, int] = {}
        """symbol -> HTTP status to answer with instead."""
        self.summaries: dict[str, dict] = {}
        """symbol -> quoteSummary modules, values as {"raw": v} like the real API."""
        self.crumb = "crumb-1"
        self.cookie_status = 404
        """fc.yahoo.com sets the cookie whatever its status; it answers 404 today."""
        self.crumb_status = 200
        self.crumbs_issued = 0
        self.expire_crumb_after_issue = False
        """The next quoteSummary rejects the crumb just issued (it expired)."""
        self.crumb_user_agents: list[str] = []
        self.quotes: dict[str, list[dict] | str] = {}
        """search text -> the quotes Yahoo's search gives (see `quote`), or a raw body; other text answers 404."""
        self.search_status = 200
        self.searched: list[str] = []
        self.profiled: list[str] = []
        self.splits: dict[str, list[tuple[date, float, float]]] = {}
        """symbol -> (day, numerator, denominator), sent as the chart's split events."""
        self.dividends: dict[str, list[tuple[date, float]]] = {}
        """symbol -> (ex-date, amount in the quoted unit), sent as the chart's dividend events when asked
        for (events=div), within period1..period2 like the real one."""
        self.charts: list[tuple[str, dict]] = []
        """(symbol, query params) of every chart request."""

    def add(self, symbol: str, currency: str | None, tz: str, bars: list[tuple], open_at: time = time(9, 30),
            kind: str = "EQUITY", first_trade: date | None = None) -> None:
        self.series[symbol] = Series(currency, tz, bars, open_at, kind, first_trade)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "fc.yahoo.com":
            return httpx.Response(self.cookie_status, headers={"set-cookie": "A3=cookie; Domain=.yahoo.com; Path=/"})
        if request.url.path == "/v1/test/getcrumb":
            self.crumb_user_agents.append(request.headers.get("user-agent", ""))
            if self.crumb_status != 200:
                return httpx.Response(self.crumb_status, text="Too Many Requests")
            self.crumbs_issued += 1
            self.crumb = f"crumb-{self.crumbs_issued}"
            return httpx.Response(200, text=self.crumb)
        if request.url.path.startswith("/v10/finance/quoteSummary/"):
            return self._summary(request)
        if request.url.path == "/v1/finance/search":
            text = request.url.params.get("q", "")
            self.searched.append(text)
            if self.search_status != 200:
                return httpx.Response(self.search_status)
            if text not in self.quotes:
                return httpx.Response(404)
            if isinstance(self.quotes[text], str):
                return httpx.Response(200, text=self.quotes[text])
            return httpx.Response(200, json={"explains": [], "count": len(self.quotes[text]),
                                              "quotes": self.quotes[text], "news": []})
        prefix = "/v8/finance/chart/"
        if not request.url.path.startswith(prefix):
            return httpx.Response(404)
        symbol = request.url.path.removeprefix(prefix)
        self.charts.append((symbol, dict(request.url.params)))
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
                     "instrumentType": s.kind,
                     **({"firstTradeDate": self._stamp(s.first_trade, s)} if s.first_trade else {})},
            "timestamp": [t for t, _ in stamps],
            "indicators": {
                "quote": [{
                    "open": [b[1] for _, b in stamps], "high": [b[2] for _, b in stamps],
                    "low": [b[3] for _, b in stamps], "close": [b[4] for _, b in stamps],
                    "volume": [b[6] for _, b in stamps],
                }],
                "adjclose": [{"adjclose": [b[5] for _, b in stamps]}],
            },
            **self._events(request, symbol, s),
        }]}})

    def _events(self, request: httpx.Request, symbol: str, s: Series) -> dict:
        events = {}
        if symbol in self.splits:
            events["splits"] = {str(self._stamp(d, s)): {"date": self._stamp(d, s), "numerator": n, "denominator": m,
                                                         "splitRatio": f"{n}:{m}"} for d, n, m in self.splits[symbol]}
        asked = request.url.params.get("events", "").split(",")
        p1, p2 = int(request.url.params.get("period1", 0)), int(request.url.params.get("period2", 2**40))
        divs = [(self._stamp(d, s), a) for d, a in self.dividends.get(symbol, ())]
        if "div" in asked and (kept := [(t, a) for t, a in divs if p1 <= t < p2]):
            events["dividends"] = {str(t): {"amount": a, "date": t} for t, a in kept}
        return {"events": events} if events else {}

    def _summary(self, request: httpx.Request) -> httpx.Response:
        unauthorised = httpx.Response(401, json={"finance": {"result": None, "error": {
            "code": "Unauthorized", "description": "Invalid Crumb"}}})
        if "A3=" not in request.headers.get("cookie", "") or request.url.params.get("crumb") != self.crumb:
            return unauthorised
        if self.expire_crumb_after_issue:
            self.expire_crumb_after_issue = False
            self.crumb = "expired"
            return unauthorised
        symbol = request.url.path.rsplit("/", 1)[1]
        self.profiled.append(symbol)
        if symbol in self.fail:
            return httpx.Response(self.fail[symbol])
        modules = self.summaries.get(symbol)
        if modules is None:
            return httpx.Response(404, json={"quoteSummary": {"result": None, "error": {
                "code": "Not Found", "description": "Quote not found for symbol"}}})
        return httpx.Response(200, json={"quoteSummary": {"result": [modules], "error": None}})

    @staticmethod
    def _stamp(day: date, s: Series) -> int:
        local = datetime.combine(day, s.open_at, tzinfo=ZoneInfo(s.tz))
        return int(local.astimezone(timezone.utc).timestamp())


def bar(day: str, close: float, adj: float | None = None, volume: int = 1000) -> tuple:
    d = date.fromisoformat(day)
    return (d, close, close, close, close, close if adj is None else adj, volume)


def quote(symbol: str, name: str, kind: str = "EQUITY", score: float = 20000.0, short: str | None = None) -> dict:
    """One quote of Yahoo's search, as it answers (captured 2026-09-24)."""
    return {"exchange": "NMS", "shortname": short or name[:31], "quoteType": kind, "symbol": symbol,
            "index": "quotes", "score": score, "longname": name, "isYahooFinance": True}


def summary(currency: str, market_cap=None, **figures) -> dict:
    """quoteSummary modules as Yahoo nests them; figure names are Yahoo's."""
    where = {
        "trailingPE": "summaryDetail", "forwardPE": "summaryDetail", "dividendYield": "summaryDetail",
        "beta": "summaryDetail", "priceToBook": "defaultKeyStatistics", "profitMargins": "financialData",
        "returnOnEquity": "financialData", "revenueGrowth": "financialData", "debtToEquity": "financialData",
    }
    modules = {"price": {"currency": currency}, "summaryDetail": {}, "defaultKeyStatistics": {}, "financialData": {}}
    if market_cap is not None:
        modules["price"]["marketCap"] = {"raw": market_cap, "fmt": "x"}
    for name, value in figures.items():
        modules[where[name]][name] = {"raw": value, "fmt": "x"}
    return modules


def profile(name: str, exchange: str, currency: str, market_cap: float | None, country: str | None = None,
            sector: str | None = None, industry: str | None = None, quote_type: str = "EQUITY",
            summary: str | None = None) -> dict:
    """The price and assetProfile modules a listing's profile reads, shaped as captured 2026-09-24."""
    price = {"longName": name, "shortName": name.upper()[:20], "exchange": exchange, "currency": currency,
             "quoteType": quote_type}
    if market_cap is not None:
        price["marketCap"] = {"raw": market_cap, "fmt": "x", "longFmt": "x"}
    asset = {k: v for k, v in (("country", country), ("sector", sector), ("industry", industry),
                               ("longBusinessSummary", summary)) if v}
    return {"price": price, **({"assetProfile": asset} if quote_type == "EQUITY" else {})}
