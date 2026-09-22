"""A fake EODHD shaped like its real API (checked against the demo key, 2026-09-22)."""

from __future__ import annotations

from datetime import date

import httpx


class FakeEodhd:
    hosts = {"eodhd.com"}

    def __init__(self, key: str = "test-key") -> None:
        self.key = key
        self.eod: dict[str, list[dict]] = {}
        """EODHD code (AAPL.US) -> rows."""
        self.explode_with: Exception | None = None
        self.fundamentals: dict[str, dict] = {}
        """EODHD code -> {section: {field: value}}."""

    def add(self, code: str, rows: list[tuple]) -> None:
        """rows: (date, close, adjusted_close, volume)."""
        self.eod[code] = [
            {"date": d, "open": c, "high": c, "low": c, "close": c, "adjusted_close": a, "volume": v}
            for d, c, a, v in rows
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.explode_with is not None:
            raise self.explode_with
        if request.url.params.get("api_token") != self.key:
            return httpx.Response(401, text="Unauthenticated")
        if request.url.path.startswith("/api/fundamentals/"):
            sections = self.fundamentals.get(request.url.path.rsplit("/", 1)[1])
            if sections is None:
                return httpx.Response(404, text="Ticker Not Found.")
            wanted = request.url.params.get("filter", "").split(",")
            return httpx.Response(200, json={k: v for k, v in sections.items() if k in wanted})
        prefix = "/api/eod/"
        if not request.url.path.startswith(prefix):
            return httpx.Response(404)
        rows = self.eod.get(request.url.path.removeprefix(prefix))
        if rows is None:
            return httpx.Response(404, text="Ticker Not Found.")
        since = request.url.params.get("from")
        if since:
            rows = [r for r in rows if date.fromisoformat(r["date"]) >= date.fromisoformat(since)]
        return httpx.Response(200, json=rows)
