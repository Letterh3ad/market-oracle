from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from oracle.store.db import transaction
from oracle.store.watchlists import WatchlistRepo

DISCOVERED_LIST = "discovered"


@dataclass(frozen=True)
class DiscoveryEvidence:
    id: int
    title: str
    url: str
    outlet: str | None
    published_at: str | None


@dataclass(frozen=True)
class Discovery:
    symbol: str
    name: str | None
    source: str | None
    """related (later: theme, mover); None for a listing put on the list by hand."""
    seed: str | None
    """The watched listing (or theme) it was found from."""
    relation: str | None
    added_at: str | None
    moved_at: str | None
    evidence: tuple[DiscoveryEvidence, ...] = ()


class DiscoveryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, symbol: str, company_key: str, name: str, source: str, seed: str, relation: str,
            evidence: Sequence[int], now: str) -> None:
        """Put the listing on the discovered list with why, in one step."""
        with transaction(self._conn):
            WatchlistRepo(self._conn).add(DISCOVERED_LIST, symbol, now)
            cur = self._conn.execute(
                "INSERT INTO discoveries (symbol, company_key, name, source, seed, relation, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (symbol, company_key, name, source, seed, relation, now))
            self._conn.executemany("INSERT OR IGNORE INTO discovery_evidence VALUES (?, ?)",
                                   [(cur.lastrowid, a) for a in evidence])

    def latest(self, symbol: str) -> Discovery | None:
        row = self._conn.execute(
            "SELECT id, symbol, name, source, seed, relation, added_at, moved_at FROM discoveries "
            "WHERE symbol = ? COLLATE NOCASE ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
        return None if row is None else Discovery(*row[1:], evidence=self._evidence(row[0]))

    def on_list(self) -> list[Discovery]:
        """What the discovered list holds, oldest first, each with its latest reasons."""
        out = []
        for item in WatchlistRepo(self._conn).items(DISCOVERED_LIST):
            found = self.latest(item.symbol)
            out.append(found or Discovery(item.symbol, item.name, None, None, None, item.added_at, None))
        return out

    def mark_moved(self, symbol: str, now: str) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE discoveries SET moved_at = ? WHERE id = "
                "(SELECT MAX(id) FROM discoveries WHERE symbol = ? COLLATE NOCASE)", (now, symbol))

    def remember_removed(self, symbol: str, company_key: str | None, name: str | None, now: str) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO discovery_removed (symbol, company_key, name, removed_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (symbol) DO NOTHING", (symbol, company_key, name, now))

    def removed(self) -> tuple[set[str], set[str]]:
        """(symbols, company keys) the user removed from the discovered list."""
        rows = self._conn.execute("SELECT symbol, company_key FROM discovery_removed").fetchall()
        return {r[0].upper() for r in rows}, {r[1] for r in rows if r[1]}

    def _evidence(self, discovery_id: int) -> tuple[DiscoveryEvidence, ...]:
        rows = self._conn.execute(
            "SELECT a.id, a.title, a.url, a.outlet, a.published_at FROM discovery_evidence e "
            "JOIN articles a ON a.id = e.article_id WHERE e.discovery_id = ? ORDER BY a.id", (discovery_id,))
        return tuple(DiscoveryEvidence(*r) for r in rows)
