"""A fake GitHub serving fja05680/sp500's membership CSV and its latest commit."""

from __future__ import annotations

from urllib.parse import unquote

import httpx

SHA = "a2430f2af0c79ddf0748e91de11bdeb1616ab5a7"


class FakeSp500:
    hosts = {"api.github.com", "raw.githubusercontent.com"}

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self.rows = rows
        """(day, comma-separated tickers) as the CSV has them."""
        self.status = 200
        self.reads = 0

    def csv(self) -> str:
        return "date,tickers\n" + "".join(f'{d},"{t}"\n' for d, t in self.rows)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"sha": SHA})
        path = unquote(request.url.path)
        if not path.endswith("S&P 500 Historical Components & Changes (Updated).csv"):
            return httpx.Response(404)
        self.reads += 1
        return httpx.Response(self.status, text=self.csv() if self.status == 200 else "")
