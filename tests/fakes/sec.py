"""A fake SEC: the company_tickers.json map and data.sec.gov submissions, shaped like the
real ones (recent filings as parallel arrays). Refuses requests without a contact, as SEC
does."""

from __future__ import annotations

import re

import httpx


class FakeSec:
    hosts = {"www.sec.gov", "data.sec.gov"}

    def __init__(self) -> None:
        self.companies: dict[str, tuple[int, str]] = {}
        """ticker -> (cik, name)"""
        self.filings: dict[int, list[tuple]] = {}
        """cik -> [(form, filing date "YYYY-MM-DD", accession "0000320193-26-000077", document, description,
        optionally items "2.02,9.01" and acceptanceDateTime "2026-07-30T16:30:12.000Z")]"""
        self.documents: dict[str, str] = {}
        """archive URL -> HTML"""
        self.failures: dict[str, httpx.Response | Exception] = {}
        """archive URL -> the response or transport error it fails with"""
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not re.search(r"\S+@\S+", request.headers.get("user-agent", "")):
            return httpx.Response(403, text="Undeclared Automated Tool")
        path = request.url.path
        if path == "/files/company_tickers.json":
            return httpx.Response(200, json={str(i): {"cik_str": cik, "ticker": t, "title": name}
                                             for i, (t, (cik, name)) in enumerate(self.companies.items())})
        if m := re.fullmatch(r"/submissions/CIK(\d{10})\.json", path):
            rows = self.filings.get(int(m.group(1)))
            if rows is None:
                return httpx.Response(404)
            names = ("form", "filingDate", "accessionNumber", "primaryDocument", "primaryDocDescription", "items",
                     "acceptanceDateTime")
            recent = {n: [r[i] if i < len(r) else "" for r in rows] for i, n in enumerate(names)}
            return httpx.Response(200, json={"cik": m.group(1), "filings": {"recent": recent, "files": []}})
        failure = self.failures.get(str(request.url))
        if isinstance(failure, Exception):
            raise failure
        if failure is not None:
            return failure
        body = self.documents.get(str(request.url))
        if body is not None:
            return httpx.Response(200, text=body, headers={"Content-Type": "text/html"})
        return httpx.Response(404)
