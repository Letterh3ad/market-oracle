"""SEC XBRL facts of the testbed's members, their filings' acceptance times, SEC's ticker map and
Yahoo split events. Lives in the research database only."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from oracle.research.xbrl import Fact
from oracle.store.db import transaction


@dataclass(frozen=True)
class XbrlMember:
    ticker: str
    cik: int | None
    facts_tried_at: str | None
    facts_note: str | None
    splits_tried_at: str | None


class XbrlRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replace_tickers(self, tickers: Mapping[str, tuple[int, str]], at: str) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM xbrl_tickers")
            self._conn.executemany("INSERT INTO xbrl_tickers (ticker, cik, name, fetched_at) VALUES (?, ?, ?, ?)",
                                   [(t, cik, name, at) for t, (cik, name) in tickers.items()])

    def tickers_fetched_at(self) -> str | None:
        return self._conn.execute("SELECT MIN(fetched_at) FROM xbrl_tickers").fetchone()[0]

    def cik(self, ticker: str) -> int | None:
        r = self._conn.execute("SELECT cik FROM xbrl_tickers WHERE ticker = ?", (ticker.upper(),)).fetchone()
        return r[0] if r else None

    def members(self) -> dict[str, XbrlMember]:
        return {r["ticker"]: XbrlMember(**dict(r)) for r in self._conn.execute(
            "SELECT ticker, cik, facts_tried_at, facts_note, splits_tried_at FROM xbrl_members")}

    def note_facts(self, ticker: str, cik: int | None, at: str, note: str | None) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO xbrl_members (ticker, cik, facts_tried_at, facts_note) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET cik = excluded.cik, facts_tried_at = excluded.facts_tried_at, "
                "facts_note = excluded.facts_note", (ticker, cik, at, note))

    def replace_facts(self, cik: int, facts: Iterable[Fact], accepted: Mapping[str, str]) -> int:
        """The company's facts and acceptance times, replacing what was stored (a refetch is whole)."""
        rows = [(cik, f.concept, f.unit, f.start.isoformat() if f.start else "", f.end.isoformat(), f.value,
                 f.accession, f.form, f.filed.isoformat()) for f in facts]
        with transaction(self._conn):
            self._conn.execute("DELETE FROM xbrl_facts WHERE cik = ?", (cik,))
            self._conn.execute("DELETE FROM xbrl_accepted WHERE cik = ?", (cik,))
            self._conn.executemany("INSERT OR IGNORE INTO xbrl_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            self._conn.executemany("INSERT OR REPLACE INTO xbrl_accepted (accession, cik, accepted) VALUES (?, ?, ?)",
                                   [(a, cik, t) for a, t in accepted.items()])
        return len(rows)

    def facts(self, cik: int) -> list[Fact]:
        return [Fact(r[0], r[1], date.fromisoformat(r[2]) if r[2] else None, date.fromisoformat(r[3]), r[4], r[5],
                     r[6], date.fromisoformat(r[7])) for r in self._conn.execute(
            "SELECT concept, unit, start, end, value, accession, form, filed FROM xbrl_facts WHERE cik = ?", (cik,))]

    def accepted(self, cik: int) -> dict[str, str]:
        return dict(self._conn.execute("SELECT accession, accepted FROM xbrl_accepted WHERE cik = ?", (cik,)))

    def fact_counts(self) -> dict[int, int]:
        return dict(self._conn.execute("SELECT cik, COUNT(*) FROM xbrl_facts GROUP BY cik"))

    def replace_splits(self, ticker: str, splits: Iterable[tuple[date, float]], at: str) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM tb_splits WHERE ticker = ?", (ticker,))
            self._conn.executemany("INSERT OR REPLACE INTO tb_splits (ticker, day, ratio) VALUES (?, ?, ?)",
                                   [(ticker, d.isoformat(), r) for d, r in splits])
            self._conn.execute("INSERT INTO xbrl_members (ticker, splits_tried_at) VALUES (?, ?) "
                               "ON CONFLICT(ticker) DO UPDATE SET splits_tried_at = excluded.splits_tried_at",
                               (ticker, at))

    def splits(self, ticker: str) -> list[tuple[date, float]]:
        return [(date.fromisoformat(d), r) for d, r in self._conn.execute(
            "SELECT day, ratio FROM tb_splits WHERE ticker = ? ORDER BY day", (ticker,))]
