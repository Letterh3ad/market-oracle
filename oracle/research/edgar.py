"""SEC EDGAR: the ticker map and a company's recent filings. SEC requires a declared
contact (name and email) in every request's User-Agent and allows at most 10 requests a
second."""

from __future__ import annotations

import html
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urljoin, urlsplit

from oracle.research.notes import MAX_NOTE_TEXT

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SUBMISSIONS = "https://data.sec.gov/submissions"
# Reports and events; insider and ownership forms (3, 4, 5, 13G, 144) are left out as noise.
KEY_FORMS = frozenset({
    "10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "20-F", "20-F/A", "40-F", "6-K", "DEF 14A", "S-1", "S-4",
})
PERIODIC = ("10-Q", "10-K")
EVENT = "8-K"
RECENT_EVENTS = timedelta(days=30)
# What one note call reads: text past it would be stored and never looked at.
FILING_TEXT = MAX_NOTE_TEXT
# Below this a report's sections are likely a stub ("incorporated by reference"), so it is
# read as if it had no item headings; and a press item only points at its exhibit.
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
# Results, and press releases under Regulation FD or "other events": the item text mostly
# points at the exhibit that says it.
PRESS_ITEMS = frozenset({"2.02", "7.01", "8.01"})
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_BOILERPLATE = re.compile(r"exhibit\s+99|furnished|deemed|incorporated\s+by\s+reference", re.I)
# Some filers pad the exhibit number (EX-99.01).
_PRESS = re.compile(r"EX-99(?:\.0*(\d+))?", re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)
_HREF = re.compile(r'href="([^"]+)"', re.I)
_TAG = re.compile(r"<[^>]+>")
# Reports without "Item N." headings (Citigroup's) still head their MD&A in words.
_WORDED = re.compile(r"^[ \t]*(?:management\S*\s+discussion\s+and\s+analysis|executive\s+summary|"
                     r"results\s+of\s+operations)\b[^\n]{0,80}$", re.I | re.M)
# The cover page ends with the filer's check-mark questions; the contents lines after it are
# short, so the first paragraph past them is the report's.
_COVER = re.compile(r"check mark", re.I)
COVER_REACH = 20_000
_PARAGRAPH = re.compile(r"^[^\n]{150,}", re.M)
# A heading with prose under it, not its table of contents line: a line of PROSE_LINE
# characters or more within its next PROSE_LINES lines (a contents entry is followed by
# page numbers and other entries).
PROSE_LINES = 5
PROSE_LINE = 80


@dataclass(frozen=True)
class Filing:
    form: str
    filed: date
    url: str
    description: str

    @property
    def title(self) -> str:
        return _named(self.form, self.description)


@dataclass(frozen=True)
class Exhibit:
    kind: str
    url: str
    description: str

    @property
    def label(self) -> str:
        return _named(self.kind, self.description)


def _named(kind: str, description: str) -> str:
    """The kind and its description, which often only repeats the kind."""
    same = description.strip().upper() in ("", kind.upper())
    return kind if same else f"{kind} {description.strip()}"


def submissions_url(cik: int) -> str:
    return f"{SUBMISSIONS}/CIK{cik:010d}.json"


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
    return parse_filing_columns(recent, cik)


def older_pages(body: object, since: date) -> list[str]:
    """The submissions pages beyond the recent list (about the last 1000 filings) that reach
    back to `since` or later, as addresses."""
    files = body.get("filings", {}).get("files", []) if isinstance(body, dict) else []
    out = []
    for f in files if isinstance(files, list) else []:
        try:
            if isinstance(f, dict) and f.get("name") and date.fromisoformat(f.get("filingTo", "")) >= since:
                out.append(f"{SUBMISSIONS}/{f['name']}")
        except (TypeError, ValueError):
            continue
    return out


def parse_filing_columns(columns: object, cik: int) -> list[Filing]:
    """Key filings from one list of the submissions' parallel columns (the recent list, or an
    older page, which holds them at its top level)."""
    columns = columns if isinstance(columns, dict) else {}
    columns = [columns.get(k, []) for k in ("form", "filingDate", "accessionNumber", "primaryDocument",
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


EARNINGS_ITEM = "2.02"
MARKET_OPEN = "09:30"


@dataclass(frozen=True)
class Release:
    """A day a company published its results (an 8-K under item 2.02)."""

    day: date
    before_open: bool | None
    """Accepted before the New York open, so that day's close already reacted; None when
    SEC gave no time."""


def parse_earnings(body: object) -> list[Release]:
    """Earnings releases from the submissions' recent list, newest first, one per day (the
    earliest filing that day times it). SEC's acceptanceDateTime is New York time despite its
    Z suffix."""
    recent = body.get("filings", {}).get("recent", {}) if isinstance(body, dict) else {}
    columns = [recent.get(k, []) for k in ("form", "filingDate", "items", "acceptanceDateTime")]
    found: dict[date, bool | None] = {}
    for form, filed, items, accepted in zip(*columns):
        if form != EVENT or EARNINGS_ITEM not in str(items or "").replace(" ", "").split(","):
            continue
        stamp = str(accepted or "")
        timed = re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d", stamp[:16]) is not None
        try:
            # Filed after 17:30 takes the next business day's filingDate; acceptance keeps the real day.
            day = date.fromisoformat(stamp[:10] if timed else filed)
        except (TypeError, ValueError):
            continue
        before = stamp[11:16] < MARKET_OPEN if timed else None
        found[day] = before if found.get(day) is None else found[day] or bool(before)
    return [Release(day, before) for day, before in sorted(found.items(), reverse=True)]


def worth_reading(filings: list[Filing], day: date) -> list[Filing]:
    """From filings listed newest first, those filed by `day` worth reading: the latest
    quarterly or annual report, then 8-Ks from the 30 days before, newest first."""
    filed = [f for f in filings if f.filed <= day]
    periodic = next((f for f in filed if f.form in PERIODIC), None)
    events = [f for f in filed if f.form == EVENT and f.filed >= day - RECENT_EVENTS]
    return ([periodic] if periodic else []) + events


def index_url(document_url: str) -> str:
    """The filing's index page on EDGAR, in the folder its document sits in, named by its
    accession number with dashes."""
    folder = document_url.rsplit("/", 1)[0]
    n = folder.rsplit("/", 1)[1]
    return f"{folder}/{n[:10]}-{n[10:12]}-{n[12:]}-index.htm"


def find_exhibit(index_html: str, index: str) -> Exhibit | None:
    """The filing's EX-99.1 from its index page, else its first EX-99.x. Only a document in
    the filing's own folder, so the index cannot send the reader elsewhere."""
    folder = index.rsplit("/", 1)[0] + "/"
    found: list[tuple[bool, Exhibit]] = []
    for row in _ROW.findall(index_html):
        cells = [html.unescape(_TAG.sub(" ", c)).strip() for c in _CELL.findall(row)]
        href = _HREF.search(row)
        press = _PRESS.fullmatch(cells[3]) if len(cells) >= 4 else None
        if not press or not href:
            continue
        url = urljoin(index, html.unescape(href.group(1)).removeprefix("/ix?doc="))
        if url.startswith(folder) and "/" not in url[len(folder):]:
            found.append((press.group(1) == "1", Exhibit(cells[3].upper(), url, cells[1])))
    return next((e for first, e in found if first), found[0][1] if found else None)


def reads_exhibit(text: str) -> bool:
    """Whether an 8-K's items are results or a press release, said in an exhibit."""
    return any(m.group(1) in PRESS_ITEMS for m in _ITEM.finditer(text))


def with_exhibit(exhibit: Exhibit, exhibit_text: str, text: str, cap: int = FILING_TEXT) -> str:
    """An 8-K read as its labelled press exhibit, then its items that say more than point at it."""
    items = _items(text, [m.start() for m in _HEADING.finditer(text)])
    others = [s for item, s in items.items() if item not in PRESS_ITEMS or _says_more(s)]
    return _fit([f"{exhibit.label}\n{exhibit_text.strip()}", *others], cap)


def _says_more(section: str) -> bool:
    """Whether a press item has MIN_SECTIONS of its own past its heading, the pointer at the
    exhibit and the "not deemed filed" legend."""
    body = section.split("\n", 1)[1] if "\n" in section else ""
    said = [s for s in _SENTENCE.split(body) if not _BOILERPLATE.search(s)]
    return sum(map(len, said)) >= MIN_SECTIONS


def key_text(form: str, text: str, cap: int = FILING_TEXT) -> str:
    """The parts of a filing's text that bear on the price: MD&A and risk factors for a 10-Q
    or 10-K, the items of an 8-K. A report without item headings is read from its MD&A
    headed in words; with nothing found, from past the cover page and table of contents."""
    heads = [m.start() for m in _HEADING.finditer(text)]
    if form in PERIODIC:
        sections = [s for s in (_longest(text, heads, _MDNA), _longest(text, heads, _RISKS)) if s]
    elif form == EVENT:
        sections = list(_items(text, heads).values())
    else:
        sections = []
    if sections and (form not in PERIODIC or sum(map(len, sections)) >= MIN_SECTIONS):
        return _fit(sections, cap)
    worded = _worded(text) if form in PERIODIC else None
    return text[worded if worded is not None else _body(text):][:cap]


def _items(text: str, heads: list[int]) -> dict[str, str]:
    """An 8-K's items but the exhibit list, each its longest occurrence."""
    items: dict[str, str] = {}
    for m in _ITEM.finditer(text):
        if m.group(1) != _EXHIBITS:
            items[m.group(1)] = max(items.get(m.group(1), ""), _section(text, heads, m.start()), key=len)
    return items


def _worded(text: str) -> int | None:
    """Where the first MD&A or results heading with prose under it starts."""
    return next((m.start() for m in _WORDED.finditer(text) if _prose(text, m.end())), None)


def _prose(text: str, at: int) -> bool:
    lines = text[at:].split("\n", PROSE_LINES + 1)[1:PROSE_LINES + 1]
    return any(len(line) >= PROSE_LINE for line in lines)


def _body(text: str) -> int:
    """The first paragraph past the cover page, which skips the table of contents too; with
    none, the start."""
    covers = [m.end() for m in _COVER.finditer(text, 0, COVER_REACH)]
    m = _PARAGRAPH.search(text, covers[-1] if covers else 0)
    return m.start() if m else 0


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


# Which ticker a company filed under: the dei:TradingSymbol on a report's cover (the XBRL viewer's
# page R1; tagged on every cover since 2019 and on many before), else its document's name, which
# filers usually start with the ticker (fb-20211231.htm), though not always and not exactly at a change.
REPORTS = frozenset({"10-K", "10-Q", "20-F", "40-F"})
_SYMBOL_CELL = re.compile(r"defref_dei_TradingSymbol.*?</td>\s*<td[^>]*>\s*([^<]*?)\s*<", re.S)
# The page's definitions pane repeats the element's name (dei_TradingSymbol) after the values.
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.\- ]{0,11}")
_DOCUMENT_TICKER = re.compile(r"([a-z]{1,6})-\d")


@dataclass(frozen=True)
class Report:
    """A periodic report (no amendments), as the submissions list it."""

    form: str
    filed: date
    accession: str
    document: str

    @property
    def document_ticker(self) -> str | None:
        """The ticker its document's name starts with (fb-20211231.htm), when it is so named."""
        m = _DOCUMENT_TICKER.match(self.document.lower())
        return m.group(1).upper() if m else None


def parse_reports(columns: object) -> list[Report]:
    """Periodic reports from the submissions' recent list or an older page, oldest first."""
    columns = columns if isinstance(columns, dict) else {}
    out = []
    for form, filed, accession, document in zip(*(columns.get(k, []) for k in (
            "form", "filingDate", "accessionNumber", "primaryDocument"))):
        try:
            if form in REPORTS and accession:
                out.append(Report(form, date.fromisoformat(filed), accession, document or ""))
        except (TypeError, ValueError):
            continue
    return sorted(out, key=lambda r: r.filed)


def recent(body: object) -> dict:
    return body.get("filings", {}).get("recent", {}) if isinstance(body, dict) else {}


def _files(body: object) -> list:
    files = body.get("filings", {}).get("files", []) if isinstance(body, dict) else []
    return files if isinstance(files, list) else []


def pages_between(body: object, start: date, end: date) -> list[str]:
    """The older submissions pages holding filings from `start` to `end`, as addresses."""
    out = []
    for f in _files(body):
        try:
            if date.fromisoformat(f["filingTo"]) >= start and date.fromisoformat(f["filingFrom"]) <= end:
                out.append(f"{SUBMISSIONS}/{f['name']}")
        except (KeyError, TypeError, ValueError):
            continue
    return out


def first_filed(body: object) -> date | None:
    """The filer's first filing day on EDGAR."""
    days = []
    for f in _files(body):
        try:
            days.append(date.fromisoformat(f["filingFrom"]))
        except (KeyError, TypeError, ValueError):
            continue
    for filed in recent(body).get("filingDate", []):
        try:
            days.append(date.fromisoformat(filed))
        except (TypeError, ValueError):
            continue
    return min(days, default=None)


def cover_url(cik: int, accession: str) -> str:
    """The XBRL viewer's first page of a filing: its cover (Document and Entity Information)."""
    return f"{ARCHIVES}/{cik}/{accession.replace('-', '')}/R1.htm"


def trading_symbols(cover_html: str) -> tuple[str, ...]:
    """The cover's dei:TradingSymbol values (one per listed class), upper case."""
    found = (html.unescape(v).strip().upper() for v in _SYMBOL_CELL.findall(cover_html))
    return tuple(dict.fromkeys(v for v in found if _SYMBOL.fullmatch(v)))
