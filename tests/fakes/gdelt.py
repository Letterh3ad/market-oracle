"""A fake GDELT DOC 2.0 API in ArtList JSON mode, shaped like the real one (seendate
"YYYYMMDDTHHMMSSZ", outlet as domain). Bad queries get plain text, as GDELT sends them."""

from __future__ import annotations

import httpx


class FakeGdelt:
    hosts = {"api.gdeltproject.org"}

    def __init__(self) -> None:
        self.results: dict[str, list[tuple]] = {}
        """query -> [(url, title, domain, datetime or None)]"""
        self.params: list[dict] = []
        self.error_text: str | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.params.append(params)
        if self.error_text:
            return httpx.Response(200, text=self.error_text)
        articles = [
            {"url": url, "url_mobile": "", "title": title, "domain": domain, "language": "English",
             "sourcecountry": "United States", "socialimage": "",
             "seendate": seen.strftime("%Y%m%dT%H%M%SZ") if seen else ""}
            for url, title, domain, seen in self.results.get(params.get("query", ""), [])
        ]
        return httpx.Response(200, json={"articles": articles} if articles else {})
