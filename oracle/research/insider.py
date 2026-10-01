"""Form 4 insider trades and typed SEC filings, point in time (next-signals 05): SEC's quarterly
Insider Transactions Data Sets (open-market purchases and sales), each issuer's submissions (Form 4
and 8-K acceptance times, 8-K items), and the pure rules the insider signal and the typed-event
study read. Numbers in, numbers out."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from bisect import bisect_left
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from oracle.research.edgar import new_york

DATASETS_PAGE = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
_DATASET = re.compile(r'href="([^"]*?/(\d{4}q[1-4])_form345\.zip)"', re.I)
FORM4 = "4"
EIGHT_K = "8-K"
FILING_FORMS = frozenset({FORM4, EIGHT_K})
"""Originals only: an amendment would count a corrected trade or event twice."""
CODES = {"P": "A", "S": "D"}
"""Open-market purchase and sale, each with the direction it must carry."""
INSIDER_ROLES = ("Director", "Officer")
ITEMS = ("2.02", "1.01", "2.01")
CLUSTER = "insider_cluster"
WINDOW = timedelta(days=90)
CLUSTER_DAYS = timedelta(days=30)
CLUSTER_MIN = 3


@dataclass(frozen=True)
class Trade:
    accession: str
    issuer_cik: int
    owner_cik: int
    owner: str
    role: str
    code: str
    traded: date
    shares: float
    price: float
    filed: date

    @property
    def value(self) -> float:
        return self.shares * self.price

    @property
    def insider(self) -> bool:
        return any(r in self.role for r in INSIDER_ROLES)


@dataclass(frozen=True)
class Filing:
    accession: str
    cik: int
    form: str
    items: str
    filed: date
    accepted: str | None
    """YYYY-MM-DDTHH:MM in New York."""


def dataset_links(page: str, base: str = "https://www.sec.gov") -> dict[str, str]:
    """quarter ("2010q1") -> its zip's address, from SEC's data sets page."""
    return {q.lower(): (href if href.startswith("http") else base + href) for href, q in _DATASET.findall(page)}


def quarter_end(quarter: str) -> date:
    y, q = int(quarter[:4]), int(quarter[-1])
    return date(y, 12, 31) if q == 4 else date(y, 3 * q + 1, 1) - timedelta(days=1)


def _day(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def _num(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _rows(z: zipfile.ZipFile, name: str) -> Iterable[dict]:
    member = next(n for n in z.namelist() if n.rsplit("/", 1)[-1].upper() == name)
    with z.open(member) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""),
                                  delimiter="\t", quoting=csv.QUOTE_NONE)


def parse_dataset(data: bytes, ciks: Collection[int]) -> list[Trade]:
    """The open-market purchases and sales in original Form 4s of the issuers in `ciks`, from one
    quarter's data set zip; each filing's first reporting owner is its filer."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        filings: dict[str, tuple[int, date]] = {}
        for r in _rows(z, "SUBMISSION.TSV"):
            if r.get("DOCUMENT_TYPE") != FORM4:
                continue
            try:
                cik = int(r["ISSUERCIK"])
            except (TypeError, ValueError):
                continue
            if cik in ciks and (filed := _day(r.get("FILING_DATE") or "")):
                filings[r["ACCESSION_NUMBER"]] = (cik, filed)
        owners: dict[str, tuple[int, str, str]] = {}
        for r in _rows(z, "REPORTINGOWNER.TSV"):
            a = r.get("ACCESSION_NUMBER")
            if a in filings and a not in owners:
                try:
                    owners[a] = (int(r["RPTOWNERCIK"]), r.get("RPTOWNERNAME") or "", r.get("RPTOWNER_RELATIONSHIP") or "")
                except (TypeError, ValueError):
                    continue
        out = []
        for r in _rows(z, "NONDERIV_TRANS.TSV"):
            a, code = r.get("ACCESSION_NUMBER"), r.get("TRANS_CODE")
            if a not in filings or a not in owners or CODES.get(code) != r.get("TRANS_ACQUIRED_DISP_CD"):
                continue
            shares, price, traded = _num(r.get("TRANS_SHARES") or ""), _num(r.get("TRANS_PRICEPERSHARE") or ""), \
                _day(r.get("TRANS_DATE") or "")
            if not (shares and shares > 0 and price and price > 0 and traded):
                continue
            cik, filed = filings[a]
            owner_cik, owner, role = owners[a]
            out.append(Trade(a, cik, owner_cik, owner, role, code, traded, shares, price, filed))
        return out


def parse_filings(columns: object, cik: int) -> list[Filing]:
    """Original Form 4s and 8-Ks from one list of the submissions' parallel columns, timed in New York."""
    columns = columns if isinstance(columns, dict) else {}
    out = []
    for form, accession, filed, items, accepted in zip(*(columns.get(k, []) for k in (
            "form", "accessionNumber", "filingDate", "items", "acceptanceDateTime"))):
        if form not in FILING_FORMS or not accession:
            continue
        try:
            day = date.fromisoformat(filed)
        except (TypeError, ValueError):
            continue
        out.append(Filing(accession, cik, form, str(items or "").replace(" ", ""), day, new_york(accepted)))
    return out


def known_day(filed: date, accepted: str | None) -> date:
    """The acceptance day in New York, else the filing date (the next business day for a filing
    accepted after 17:30, so never earlier)."""
    return date.fromisoformat(accepted[:10]) if accepted else filed


def item_events(filings: Iterable[Filing]) -> dict[str, list[date]]:
    """item -> the sorted distinct known days of the original 8-Ks listing it."""
    out: dict[str, set[date]] = {i: set() for i in ITEMS}
    for f in filings:
        if f.form == EIGHT_K:
            listed = f.items.split(",")
            for i in ITEMS:
                if i in listed:
                    out[i].add(known_day(f.filed, f.accepted))
    return {i: sorted(days) for i, days in out.items()}


Buys = tuple[list[date], list[int]]
"""(known days sorted, the insider owner CIK of each) of one issuer's open-market purchases."""


def buys(trades: Iterable[Trade], accepted: Mapping[str, str]) -> Buys:
    """An issuer's insider purchases by known day, one per filing."""
    seen: dict[str, tuple[date, int]] = {}
    for t in trades:
        if t.code == "P" and t.insider and t.accession not in seen:
            seen[t.accession] = (known_day(t.filed, accepted.get(t.accession)), t.owner_cik)
    rows = sorted(seen.values())
    return [d for d, _ in rows], [o for _, o in rows]


def distinct_buyers(b: Buys, first: date, last: date) -> int:
    """Insiders with a purchase known from `first` to `last`, both counted."""
    days, owners = b
    return len(set(owners[bisect_left(days, first):bisect_left(days, last + timedelta(days=1))]))


def insider_score(b: Buys, day: date) -> int:
    """Distinct insiders buying, known on a day from `day` - 90 to the day before."""
    return distinct_buyers(b, day - WINDOW, day - timedelta(days=1))


def clusters(b: Buys) -> list[date]:
    """Known days on which CLUSTER_MIN or more insiders have a purchase known in the 30 days ending
    that day, at most one per CLUSTER_DAYS."""
    out: list[date] = []
    for d in sorted(set(b[0])):
        if out and d - out[-1] < CLUSTER_DAYS:
            continue
        if distinct_buyers(b, d - CLUSTER_DAYS + timedelta(days=1), d) >= CLUSTER_MIN:
            out.append(d)
    return out


def first_after(days: Sequence[date], day: date) -> int:
    """Index of the first of sorted `days` after `day` (the next session)."""
    return bisect_left(days, day + timedelta(days=1))
