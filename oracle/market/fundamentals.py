"""Company fundamentals: one dated snapshot per company, with a plain-English line for
every figure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from oracle.market import eodhd, yahoo
from oracle.market.bars import major_unit
from oracle.market.sources import Instrument, NoData, SourceFailed, first_present
from oracle.net.client import RateLimitedClient


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


_YAHOO_MODULES = "price,summaryDetail,defaultKeyStatistics,financialData"


class YahooFundamentals:
    name = yahoo.SOURCE

    def __init__(self, client: RateLimitedClient) -> None:
        self._summary = yahoo.QuoteSummary(client)

    async def fundamentals(self, instrument: Instrument, today: date) -> Fundamentals:
        return _from_yahoo(instrument.symbol, today, await self._summary.modules(instrument.symbol, _YAHOO_MODULES))


def _from_yahoo(symbol: str, today: date, modules: dict) -> Fundamentals:
    def raw(module: str, key: str) -> float | None:
        value = (modules.get(module) or {}).get(key)
        value = value.get("raw") if isinstance(value, dict) else value
        return float(value) if isinstance(value, (int, float)) else None

    debt_pct = raw("financialData", "debtToEquity")
    quoted = (modules.get("price") or {}).get("currency")
    return Fundamentals(
        symbol=symbol, as_of=today, source=yahoo.SOURCE,
        # Market cap is in the major unit even when the price is quoted in pence
        # (checked on BP.L: 8.4e10 at 542p, i.e. pounds).
        currency=major_unit(quoted)[0] if quoted else None,
        market_cap=first_present(raw("price", "marketCap"), raw("summaryDetail", "marketCap")),
        pe=raw("summaryDetail", "trailingPE"),
        forward_pe=first_present(raw("summaryDetail", "forwardPE"), raw("defaultKeyStatistics", "forwardPE")),
        pb=raw("defaultKeyStatistics", "priceToBook"),
        dividend_yield=raw("summaryDetail", "dividendYield"),
        profit_margin=raw("financialData", "profitMargins"),
        roe=raw("financialData", "returnOnEquity"),
        revenue_growth=raw("financialData", "revenueGrowth"),
        debt_to_equity=None if debt_pct is None else debt_pct / 100,  # Yahoo gives a percentage
        beta=first_present(raw("summaryDetail", "beta"), raw("defaultKeyStatistics", "beta")),
    )


class EodhdFundamentals:
    name = eodhd.SOURCE

    def __init__(self, client: eodhd.EodhdClient) -> None:
        self._eodhd = client

    async def fundamentals(self, instrument: Instrument, today: date) -> Fundamentals:
        code = eodhd.eodhd_code(instrument.symbol)
        if code is None:
            raise NoData("exchange not covered")
        body = await self._eodhd.get_json(f"fundamentals/{code}", {"filter": "Highlights,Valuation,Technicals"})
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
