"""SEC EDGAR: the ticker map and a company's recent filings. SEC requires a declared
contact (name and email) in every request's User-Agent and allows at most 10 requests a
second."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlsplit

from oracle.research.notes import MAX_NOTE_TEXT

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
# Reports and events; insider and ownership forms (3, 4, 5, 13G, 144) are left out as noise.
KEY_FORMS = frozenset({
    "10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "20-F", "20-F/A", "40-F", "6-K", "DEF 14A", "S-1", "S-4",
})
PERIODIC = ("10-Q", "10-K")
EVENT = "8-K"
RECENT_EVENTS = timedelta(days=30)
# What one note call reads: text past it would be stored and never looked at.
FILING_TEXT = MAX_NOTE_TEXT
# Below this a report's sections are likely a stub ("incorporated by reference"), so the
# start of the document says more.
MIN_SECTIONS = 500

# Headings start a line; the item number and its title may sit on separate lines, and the
# table of contents repeats every heading.
_SEP = r"\s*[.:\-\u2013\u2014]?\s*"
_HEADING = re.compile(r"^[ \t]*(?:item\s*\d{1,2}(?:\.\d{2}|[a-d])?\b|signatures?[ \t]*$)", re.I | re.M)
_MDNA = re.compile(rf"^[ \t]*item\s*\d{{1,2}}{_SEP}management\S*\s+discussion", re.I | re.M)
_RISKS = re.compile(rf"^[ \t]*item\s*1a{_SEP}risk\s+factors", re.I | re.M)
_ITEM = re.compile(r"^[ \t]*item\s*(\d\.\d{2})", re.I | re.M)
# Only lists the exhibits.
_EXHIBITS = "9.01"


@dataclass(frozen=True)
class Filing:
    form: str
    filed: date
    url: str
    description: str

    @property
    def title(self) -> str:
        """The form and its description, which often only repeats the form."""
        same = self.description.strip().upper() in ("", self.form.upper())
        return self.form if same else f"{self.form} {self.description.strip()}"


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


def worth_reading(filings: list[Filing], day: date) -> list[Filing]:
    """From filings listed newest first, those filed by `day` worth reading: the latest
    quarterly or annual report, then 8-Ks from the 30 days before, newest first."""
    filed = [f for f in filings if f.filed <= day]
    periodic = next((f for f in filed if f.form in PERIODIC), None)
    events = [f for f in filed if f.form == EVENT and f.filed >= day - RECENT_EVENTS]
    return ([periodic] if periodic else []) + events


def key_text(form: str, text: str, cap: int = FILING_TEXT) -> str:
    """The parts of a filing's text that bear on the price: MD&A and risk factors for a 10-Q
    or 10-K, the items of an 8-K. With none found, the start of the text, so a filing laid
    out unusually is still read."""
    heads = [m.start() for m in _HEADING.finditer(text)]
    if form in PERIODIC:
        sections = [s for s in (_longest(text, heads, _MDNA), _longest(text, heads, _RISKS)) if s]
    elif form == EVENT:
        items: dict[str, str] = {}
        for m in _ITEM.finditer(text):
            if m.group(1) != _EXHIBITS:
                section = _section(text, heads, m.start())
                items[m.group(1)] = max(items.get(m.group(1), ""), section, key=len)
        sections = list(items.values())
    else:
        sections = []
    if not sections or (form in PERIODIC and sum(map(len, sections)) < MIN_SECTIONS):
        return text[:cap]
    return _fit(sections, cap)


def _section(text: str, heads: list[int], start: int) -> str:
    i = bisect_right(heads, start)
    return text[start:heads[i] if i < len(heads) else len(text)].strip()


def _longest(text: str, heads: list[int], heading: re.Pattern) -> str:
    """The occurrence of a heading with the most text under it: the others are the table of
    contents and cross-references."""
    return max((_section(text, heads, m.start()) for m in heading.finditer(text)), key=len, default="")


def _fit(sections: list[str], cap: int) -> str:
    """Sections joined within `cap`, sharing it evenly; a short one leaves its room to the rest."""
    left = cap - 2 * (len(sections) - 1)
    sizes = [0] * len(sections)
    for k, i in enumerate(sorted(range(len(sections)), key=lambda i: len(sections[i]))):
        sizes[i] = min(len(sections[i]), left // (len(sections) - k))
        left -= sizes[i]
    return "\n\n".join(s[:n].rstrip() for s, n in zip(sections, sizes))
