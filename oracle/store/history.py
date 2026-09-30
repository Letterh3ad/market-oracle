from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from oracle.store.db import transaction


@dataclass(frozen=True)
class Window:
    symbol: str
    source: str
    start: date
    end: date
    """Exclusive."""
    found: int
    """Results the source gave."""
    kept: int
    """Dated inside the window, stored."""
    undated: int
    outside: int
    """Dated outside the window asked for, dropped."""
    note: str | None = None


@dataclass(frozen=True)
class QuarterCount:
    symbol: str
    quarter: str
    """"2023-Q2"."""
    source: str
    articles: int
    outlets: int


class HistoryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def done(self, source: str) -> dict[tuple[str, date, date], Window]:
        """(casefolded symbol, start, end) -> the finished window."""
        rows = self._conn.execute(
            "SELECT symbol, source, start, end, found, kept, undated, outside, note FROM history_windows "
            "WHERE source = ?", (source,))
        out = {}
        for r in rows:
            w = Window(r[0], r[1], date.fromisoformat(r[2]), date.fromisoformat(r[3]), *r[4:])
            out[(w.symbol.casefold(), w.start, w.end)] = w
        return out

    def finish(self, w: Window, article_ids: Iterable[int], at: str) -> None:
        """The window and the articles it kept, in one transaction: a crash leaves neither."""
        with transaction(self._conn):
            self._conn.executemany("INSERT OR IGNORE INTO history_articles (symbol, article_id) VALUES (?, ?)",
                                   [(w.symbol, i) for i in article_ids])
            self._conn.execute(
                "INSERT OR REPLACE INTO history_windows (symbol, source, start, end, found, kept, undated, outside, "
                "note, done_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (w.symbol, w.source, w.start.isoformat(), w.end.isoformat(), w.found, w.kept, w.undated, w.outside,
                 w.note, at))

    def article_ids(self, symbol: str) -> list[int]:
        return [r[0] for r in self._conn.execute(
            "SELECT h.article_id FROM history_articles h JOIN articles a ON a.id = h.article_id "
            "WHERE h.symbol = ? ORDER BY a.published_at, a.id", (symbol,))]

    def quarters(self, symbols: list[str]) -> list[QuarterCount]:
        """Stored articles per company, calendar quarter of publication and source."""
        if not symbols:
            return []
        rows = self._conn.execute(
            "SELECT h.symbol, substr(a.published_at, 1, 4) || '-Q' || ((CAST(substr(a.published_at, 6, 2) AS INTEGER)"
            " + 2) / 3), a.source, COUNT(*), COUNT(DISTINCT COALESCE(a.outlet, a.site)) FROM history_articles h "
            f"JOIN articles a ON a.id = h.article_id WHERE h.symbol IN ({','.join('?' * len(symbols))}) "
            "AND a.published_at IS NOT NULL GROUP BY 1, 2, 3", symbols)
        return [QuarterCount(*r) for r in rows]
