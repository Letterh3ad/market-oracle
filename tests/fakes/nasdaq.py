"""A fake Nasdaq dividends endpoint shaped like the real one (captured 2026-10-02 for ACWI)."""

from __future__ import annotations

from datetime import date

import httpx


class FakeNasdaq:
    hosts = {"api.nasdaq.com"}

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], list[tuple[date, float, date | None]]] = {}
        """(symbol, asset class) -> (ex-date, amount, pay date or None for "N/A")."""
        self.asked: list[tuple[str, str]] = []
        self.status = 200

    def add(self, symbol: str, asset: str, rows: list[tuple[date, float, date | None]]) -> None:
        self.rows[(symbol, asset)] = rows

    def handle(self, request: httpx.Request) -> httpx.Response:
        symbol = request.url.path.split("/")[3]
        asset = request.url.params.get("assetclass", "")
        self.asked.append((symbol, asset))
        if self.status != 200:
            return httpx.Response(self.status)
        rows = self.rows.get((symbol, asset))
        if rows is None:
            return httpx.Response(200, json={"data": None, "message": None,
                                             "status": {"rCode": 200, "bCodeMessage": None}})

        def us(d: date | None) -> str:
            return d.strftime("%m/%d/%Y") if d else "N/A"

        return httpx.Response(200, json={"data": {"exDividendDate": us(rows[0][0]) if rows else "N/A", "dividends": {
            "asOf": None, "headers": {},
            "rows": [{"exOrEffDate": us(ex), "type": "Cash", "amount": f"${amount:.6f}", "declarationDate": "N/A",
                      "recordDate": us(ex), "paymentDate": us(pay), "currency": "USD"} for ex, amount, pay in rows]}},
            "message": None, "status": {"rCode": 200}})
