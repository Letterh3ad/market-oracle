from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from oracle.store.db import transaction


@dataclass(frozen=True)
class WatchItem:
    symbol: str
    name: str | None
    exchange: str | None
    currency: str | None
    country: str | None
    added_at: str
    delisted: bool
    missing: bool
    """No longer in the universe since a refresh."""


@dataclass(frozen=True)
class WatchlistSummary:
    name: str
    items: int
    missing: int


class WatchlistRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def name_of(self, name: str) -> str | None:
        """The list's name as created, matched without case."""
        row = self._conn.execute("SELECT name FROM watchlists WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def create(self, watchlist: str, now: str) -> tuple[str, bool]:
        """(the list's stored name, whether it was new)."""
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO watchlists (name, created_at) VALUES (?, ?)", (watchlist, now)
            )
            return self.name_of(watchlist), cur.rowcount > 0

    def add(self, watchlist: str, symbol: str, now: str) -> tuple[str, bool]:
        """(the list's stored name, whether the symbol was new to it)."""
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR IGNORE INTO watchlists (name, created_at) VALUES (?, ?)", (watchlist, now)
            )
            stored = self.name_of(watchlist)
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO watchlist_items (watchlist, symbol, added_at) VALUES (?, ?, ?)",
                (stored, symbol, now),
            )
            return stored, cur.rowcount > 0

    def remove(self, watchlist: str, symbol: str) -> bool:
        with transaction(self._conn):
            cur = self._conn.execute(
                "DELETE FROM watchlist_items WHERE watchlist = ? COLLATE NOCASE AND symbol = ? COLLATE NOCASE",
                (watchlist, symbol),
            )
            return cur.rowcount > 0

    def items(self, watchlist: str) -> list[WatchItem]:
        rows = self._conn.execute(
            """SELECT i.symbol, COALESCE(NULLIF(p.name, ''), NULLIF(l.name, '')) AS name,
                      l.exchange, l.currency, p.country, i.added_at,
                      COALESCE(l.delisted, 0) AS delisted, l.symbol IS NULL AS missing
               FROM watchlist_items i
               LEFT JOIN listings l ON l.symbol = i.symbol
               LEFT JOIN listings p ON p.company_key = l.company_key AND p.is_primary
               WHERE i.watchlist = ? COLLATE NOCASE
               ORDER BY i.rowid""",
            (watchlist,),
        )
        return [WatchItem(*r[:6], delisted=bool(r[6]), missing=bool(r[7])) for r in rows]

    def all_symbols(self) -> list[str]:
        """Every watched symbol once, whichever lists hold it."""
        return [r[0] for r in self._conn.execute("SELECT DISTINCT symbol FROM watchlist_items ORDER BY symbol")]

    def summaries(self) -> list[WatchlistSummary]:
        rows = self._conn.execute(
            """SELECT w.name, COUNT(i.symbol), COUNT(i.symbol) - COUNT(l.symbol)
               FROM watchlists w
               LEFT JOIN watchlist_items i ON i.watchlist = w.name
               LEFT JOIN listings l ON l.symbol = i.symbol
               GROUP BY w.name ORDER BY w.name COLLATE NOCASE"""
        )
        return [WatchlistSummary(*r) for r in rows]
