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
        self.older: dict[int, list[tuple[str, str, list[tuple]]]] = {}
        """cik -> older submissions pages [(filingFrom, filingTo, rows shaped as `filings`)]"""
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
        if m := re.fullmatch(r"/submissions/CIK(\d{10})-submissions-(\d{3})\.json", path):
            pages = self.older.get(int(m.group(1)), [])
            n = int(m.group(2))
            return httpx.Response(200, json=_columns(pages[n - 1][2])) if 0 < n <= len(pages) else httpx.Response(404)
        if m := re.fullmatch(r"/submissions/CIK(\d{10})\.json", path):
            cik = int(m.group(1))
            rows = self.filings.get(cik)
            if rows is None:
                return httpx.Response(404)
            files = [{"name": f"CIK{cik:010d}-submissions-{i:03d}.json", "filingFrom": a, "filingTo": b}
                     for i, (a, b, _) in enumerate(self.older.get(cik, []), 1)]
            return httpx.Response(200, json={"cik": m.group(1), "filings": {"recent": _columns(rows), "files": files}})
        failure = self.failures.get(str(request.url))
        if isinstance(failure, Exception):
            raise failure
        if failure is not None:
            return failure
        body = self.documents.get(str(request.url))
        if body is not None:
            return httpx.Response(200, text=body, headers={"Content-Type": "text/html"})
        return httpx.Response(404)


def cover(*symbols: str) -> str:
    """A report's XBRL cover page (R1.htm) tagging these trading symbols, shaped as captured 2026-10-01."""
    rows = "".join(
        '<tr class="re"><td class="pl" style="border-bottom: 0px;" valign="top"><a class="a" href="javascript:void(0);" '
        "onclick=\"top.Show.showAR( this, 'defref_dei_TradingSymbol', window );\">Trading Symbol</a></td>\n"
        f'<td class="text">{s}<span></span>\n</td>\n<td class="text">&#160;<span></span>\n</td></tr>\n' for s in symbols)
    return f"<html><body><table><tr><th>Document and Entity Information</th></tr>{rows}</table></body></html>"


def _columns(rows: list[tuple]) -> dict:
    names = ("form", "filingDate", "accessionNumber", "primaryDocument", "primaryDocDescription", "items",
             "acceptanceDateTime")
    return {n: [r[i] if i < len(r) else "" for r in rows] for i, n in enumerate(names)}
