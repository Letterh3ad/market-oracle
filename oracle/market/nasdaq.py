"""Dividend pay dates for US listings from Nasdaq's quote API, which Yahoo's chart does not
give (it has the ex-date and amount only). Unofficial and unmetered, so asked sparingly: only
for a dividend a paper ledger is owed."""

from __future__ import annotations

from datetime import date, datetime
from urllib.parse import quote

from oracle.market import sources
from oracle.market.yahoo import BROWSER_UA
from oracle.net.client import RateLimitedClient

SOURCE = "nasdaq"
URL = "https://api.nasdaq.com/api/quote/{symbol}/dividends"
# The API answers an empty table for the wrong asset class rather than an error.
ASSET_CLASSES = ("stocks", "etf")


async def pay_dates(client: RateLimitedClient, symbol: str) -> dict[date, date]:
    """Pay date by ex-date for every cash dividend Nasdaq lists; empty when it lists none."""
    for asset in ASSET_CLASSES:
        resp = await sources.get(client, SOURCE, URL.format(symbol=quote(symbol, safe="")), {"assetclass": asset},
                                 headers={"User-Agent": BROWSER_UA, "Accept": "application/json"})
        rows = (((resp.json() or {}).get("data") or {}).get("dividends") or {}).get("rows") or []
        if rows:
            return parse_rows(rows)
    return {}


def parse_rows(rows: list[dict]) -> dict[date, date]:
    out = {}
    for r in rows:
        if (r.get("type") or "Cash").lower() != "cash":
            continue
        ex, pay = _day(r.get("exOrEffDate")), _day(r.get("paymentDate"))
        if ex and pay:
            out[ex] = pay
    return out


def _day(text) -> date | None:
    try:
        return datetime.strptime(text, "%m/%d/%Y").date()
    except (TypeError, ValueError):
        return None
