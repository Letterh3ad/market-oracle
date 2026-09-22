"""Company fundamentals: one dated snapshot per company, with a plain-English line for
every figure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol
from urllib.parse import quote

from oracle.market import eodhd
from oracle.market.bars import major_unit
from oracle.market.sources import Instrument, NoData, SourceFailed
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed


@dataclass(frozen=True)
class Fundamentals:
    symbol: str
    as_of: date
    source: str
    currency: str | None
    """Of market_cap; the ratios have no currency."""
    market_cap: float | None = None
    pe: float | None = None
    forward_pe: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None
    profit_margin: float | None = None
    roe: float | None = None
    revenue_growth: float | None = None
    debt_to_equity: float | None = None
    """A plain ratio: 0.78 means 78p of debt per £1 of shareholders' equity."""
    beta: float | None = None


@dataclass(frozen=True)
class Figure:
    name: str
    label: str
    kind: str
    """money, ratio (x) or pct."""
    meaning: str


FIGURES = (
    Figure("market_cap", "Market cap", "money",
           "What the whole company is worth on the stock market: share price times shares."),
    Figure("pe", "P/E", "ratio",
           "Price over the last year's earnings per share: years of current profit you pay for. "
           "Lower is cheaper; compare within a sector."),
    Figure("forward_pe", "Forward P/E", "ratio",
           "The same, against analysts' forecast of next year's earnings."),
    Figure("pb", "P/B", "ratio",
           "Price over book value (assets minus debts) per share. Below 1 means the market "
           "values it at less than its accounts do."),
    Figure("dividend_yield", "Dividend yield", "pct",
           "A year's dividends as a share of the price: the cash return if dividends stay the same."),
    Figure("profit_margin", "Profit margin", "pct",
           "The share of revenue left as profit after every cost."),
    Figure("roe", "Return on equity", "pct",
           "Profit as a share of the shareholders' money in the business: how well it turns "
           "owners' capital into profit."),
    Figure("revenue_growth", "Revenue growth", "pct",
           "Revenue against the same quarter a year earlier."),
    Figure("debt_to_equity", "Debt/equity", "ratio",
           "Debt for every 1 of shareholders' equity. Above 2 is heavily borrowed for most sectors "
           "(banks and utilities run higher)."),
    Figure("beta", "Beta", "ratio",
           "How much the price swings with the whole market: 1 moves with it, above 1 swings "
           "more, below 1 less."),
)


class FundamentalsSource(Protocol):
    name: str

    async def fundamentals(self, instrument: Instrument, today: date) -> Fundamentals: ...


# Yahoo answers its crumb endpoint with 429 to non-browser user agents (checked
# 2026-09-22), so the crumb flow presents as a browser, as yfinance does.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36"
)
_YAHOO_MODULES = "price,summaryDetail,defaultKeyStatistics,financialData"


class YahooFundamentals:
    """quoteSummary needs a session cookie (from fc.yahoo.com) and a crumb bound to it.
    The client keeps cookies, so one instance serves one session; a rejected crumb is
    renewed once."""

    name = "yahoo"
    SOURCE = "yahoo"

    def __init__(self, client: RateLimitedClient) -> None:
        self._client = client
        self._crumb: str | None = None

    async def fundamentals(self, instrument: Instrument, today: date) -> Fundamentals:
        for attempt in range(2):
            if self._crumb is None:
                self._crumb = await self._new_crumb()
            resp = await self._get(
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{quote(instrument.symbol, safe='')}",
                {"modules": _YAHOO_MODULES, "crumb": self._crumb},
            )
            if resp.status_code != 401:
                break
            self._crumb = None
        else:
            raise SourceFailed("crumb rejected twice")
        if resp.status_code == 404:
            raise NoData("no fundamentals")
        if resp.status_code != 200:
            raise SourceFailed(f"HTTP {resp.status_code}")
        try:
            return _from_yahoo(instrument.symbol, today, resp.json()["quoteSummary"]["result"][0])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise SourceFailed(f"unreadable quoteSummary ({type(exc).__name__})") from None

    async def _new_crumb(self) -> str:
        await self._get("https://fc.yahoo.com")  # answers 404 but sets the session cookie
        resp = await self._get("https://query2.finance.yahoo.com/v1/test/getcrumb")
        if resp.status_code != 200 or not resp.text.strip():
            raise SourceFailed(f"no crumb (HTTP {resp.status_code})")
        return resp.text.strip()

    async def _get(self, url: str, params: dict | None = None):
        try:
            return await self._client.get(self.SOURCE, url, params=params, headers={"User-Agent": BROWSER_UA})
        except (RequestFailed, CircuitOpen) as exc:
            detail = str(exc).removeprefix(f"{self.SOURCE}: ")
            raise SourceFailed(f"no crumb ({detail})" if "getcrumb" in url else detail) from None


def _from_yahoo(symbol: str, today: date, modules: dict) -> Fundamentals:
    def raw(module: str, key: str) -> float | None:
        value = (modules.get(module) or {}).get(key)
        value = value.get("raw") if isinstance(value, dict) else value
        return float(value) if isinstance(value, (int, float)) else None

    debt_pct = raw("financialData", "debtToEquity")
    quoted = (modules.get("price") or {}).get("currency")
    return Fundamentals(
        symbol=symbol, as_of=today, source="yahoo",
        # Market cap is in the major unit even when the price is quoted in pence.
        currency=major_unit(quoted)[0] if quoted else None,
        market_cap=raw("price", "marketCap") or raw("summaryDetail", "marketCap"),
        pe=raw("summaryDetail", "trailingPE"),
        forward_pe=raw("summaryDetail", "forwardPE") or raw("defaultKeyStatistics", "forwardPE"),
        pb=raw("defaultKeyStatistics", "priceToBook"),
        dividend_yield=raw("summaryDetail", "dividendYield"),
        profit_margin=raw("financialData", "profitMargins"),
        roe=raw("financialData", "returnOnEquity"),
        revenue_growth=raw("financialData", "revenueGrowth"),
        debt_to_equity=None if debt_pct is None else debt_pct / 100,  # Yahoo gives a percentage
        beta=raw("summaryDetail", "beta") or raw("defaultKeyStatistics", "beta"),
    )


class EodhdFundamentals:
    name = "eodhd"

    def __init__(self, client: RateLimitedClient, key: str) -> None:
        self._eodhd = eodhd.EodhdPrices(client, key)

    async def fundamentals(self, instrument: Instrument, today: date) -> Fundamentals:
        code = eodhd.eodhd_code(instrument.symbol)
        if code is None:
            raise NoData("exchange not covered")
        body = await self._eodhd.get_json(
            f"https://eodhd.com/api/fundamentals/{code}", {"filter": "Highlights,Valuation,Technicals"}, code
        )
        if not isinstance(body, dict):
            raise SourceFailed("unreadable response")

        def get(section: str, key: str) -> float | None:
            value = (body.get(section) or {}).get(key)
            return float(value) if isinstance(value, (int, float)) else None

        return Fundamentals(
            symbol=instrument.symbol, as_of=today, source=self.name,
            currency=major_unit(instrument.currency)[0] if instrument.currency else None,
            market_cap=get("Highlights", "MarketCapitalization"),
            pe=get("Highlights", "PERatio"),
            forward_pe=get("Valuation", "ForwardPE"),
            pb=get("Valuation", "PriceBookMRQ"),
            dividend_yield=get("Highlights", "DividendYield"),
            profit_margin=get("Highlights", "ProfitMargin"),
            roe=get("Highlights", "ReturnOnEquityTTM"),
            revenue_growth=get("Highlights", "QuarterlyRevenueGrowthYOY"),
            beta=get("Technicals", "Beta"),
        )
