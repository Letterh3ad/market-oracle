"""A fake GitHub serving FinanceDatabase files built from hand-written rows."""

from __future__ import annotations

import bz2
import csv
import io

import httpx

EQUITY_COLUMNS = [
    "symbol", "name", "summary", "currency", "sector", "industry_group", "industry",
    "exchange", "mic", "market", "country", "state", "city", "zipcode", "website",
    "market_cap", "isin", "cusip", "figi", "composite_figi", "shareclass_figi", "delisted",
]


def equity(symbol, name, country="United States", exchange="NMS", **fields) -> dict:
    row = dict.fromkeys(EQUITY_COLUMNS, "")
    row.update(symbol=symbol, name=name, country=country, exchange=exchange, delisted="False")
    row.update({k: str(v) for k, v in fields.items()})
    return row


def bz2_csv(rows: list[dict], columns: list[str] = EQUITY_COLUMNS) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return bz2.compress(buf.getvalue().encode("utf-8"))


APPLE = "US0378331005"

# 7 listings, 4 companies, 1 delisted.
BASIC = [
    equity("AAPL", "Apple Inc.", isin=APPLE, sector="Information Technology", market_cap="Mega Cap"),
    equity("APC.DE", "Apple Inc.", exchange="GER", isin=APPLE, currency="EUR"),
    equity("AAPL.MI", "Apple Inc.", exchange="MIL", isin=APPLE, currency="EUR"),
    equity("7203.T", "Toyota Motor Corporation", "Japan", "JPX", currency="JPY"),
    equity("TM", "Toyota Motor Corporation", "Japan", "NYQ"),
    equity("OLD.F", "Oldco AG", "Germany", "FRA", delisted="True"),
    equity("NONAME.F", "", "Germany", "FRA"),
]


class FakeFinanceDatabase:
    def __init__(self, equities: list[dict], sha: str = "abc123") -> None:
        self.sha = sha
        self.files: dict[str, bytes | int] = {"equities.bz2": bz2_csv(equities)}
        self.commit_response: httpx.Response | None = None
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "api.github.com" and path == "/repos/JerBouma/FinanceDatabase/commits/main":
            return self.commit_response or httpx.Response(200, json={"sha": self.sha})
        prefix = f"/JerBouma/FinanceDatabase/{self.sha}/compression/"
        if request.url.host == "raw.githubusercontent.com" and path.startswith(prefix):
            body = self.files.get(path.removeprefix(prefix), 404)
            if isinstance(body, int):
                return httpx.Response(body)
            return httpx.Response(200, content=body)
        return httpx.Response(404)
