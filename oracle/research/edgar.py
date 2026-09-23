"""SEC EDGAR: the ticker map and a company's recent filings. SEC requires a declared
contact (name and email) in every request's User-Agent and allows at most 10 requests a
second."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
# Reports and events; insider and ownership forms (3, 4, 5, 13G, 144) are left out as noise.
KEY_FORMS = frozenset({
    "10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "20-F", "20-F/A", "40-F", "6-K", "DEF 14A", "S-1", "S-4",
})


@dataclass(frozen=True)
class Filing:
    form: str
    filed: date
    url: str
    description: str


def submissions_url(cik: int) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik:010d}.json"


def user_agent(contact: str) -> str:
    return f"market-oracle/0.1 {contact}"


def is_sec(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "sec.gov" or host.endswith(".sec.gov")


def parse_tickers(body: object) -> dict[str, tuple[int, str]]:
    """ticker -> (CIK, company name)."""
    rows = body.values() if isinstance(body, dict) else []
    return {str(r["ticker"]).upper(): (int(r["cik_str"]), str(r["title"])) for r in rows
            if isinstance(r, dict) and r.get("ticker") and r.get("cik_str")}


def parse_filings(body: object, cik: int) -> list[Filing]:
    """Key filings from the submissions' recent list, as filed (newest first)."""
    recent = body.get("filings", {}).get("recent", {}) if isinstance(body, dict) else {}
    columns = [recent.get(k, []) for k in ("form", "filingDate", "accessionNumber", "primaryDocument",
                                            "primaryDocDescription")]
    out = []
    for form, filed, accession, document, description in zip(*columns):
        if form not in KEY_FORMS or not document:
            continue
        try:
            day = date.fromisoformat(filed)
        except (TypeError, ValueError):
            continue
        url = f"{ARCHIVES}/{cik}/{accession.replace('-', '')}/{document}"
        out.append(Filing(form, day, url, description or ""))
    return out
