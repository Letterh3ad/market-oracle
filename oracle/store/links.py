from __future__ import annotations

import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from oracle.store.db import transaction
from oracle.store.discoveries import DiscoveryEvidence

_COLUMNS = "id, symbol, other, relation, source, first_seen, last_seen, deleted_at"


@dataclass(frozen=True)
class StoredLink:
    """`other` is `relation` of `symbol` (a supplier of it, say), as first found by `source`."""

    id: int
    symbol: str
    other: str
    relation: str
    source: str
    first_seen: str
    last_seen: str
    deleted_at: str | None
    evidence: tuple[DiscoveryEvidence, ...] = ()


class LinkRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(self, symbol: str, other: str, relation: str, source: str, evidence: Sequence[int], now: str) -> None:
        """New, or seen again with any new evidence; a deleted link stays deleted."""
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO company_links (symbol, other, relation, source, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (symbol, other, relation) DO UPDATE SET last_seen = ?",
                (symbol, other, relation, source, now, now, now))
            (link_id,) = self._conn.execute(
                "SELECT id FROM company_links WHERE symbol = ? AND other = ? AND relation = ?",
                (symbol, other, relation)).fetchone()
            self._conn.executemany("INSERT OR IGNORE INTO company_link_evidence VALUES (?, ?)",
                                   [(link_id, a) for a in evidence])

    def of(self, symbols: Collection[str], seen_before: str | None = None) -> list[StoredLink]:
        """Live links with any of `symbols` at either end, most evidence first, then newest.
        `seen_before` (an ISO day) leaves out links first seen on or after it."""
        marks = ", ".join("?" * len(symbols))
        rows = self._conn.execute(
            f"SELECT {_COLUMNS}, (SELECT COUNT(*) FROM company_link_evidence e WHERE e.link_id = l.id) AS n "
            f"FROM company_links l WHERE deleted_at IS NULL AND (symbol IN ({marks}) OR other IN ({marks})) "
            "AND (? IS NULL OR first_seen < ?) ORDER BY n DESC, last_seen DESC, id",
            (*symbols, *symbols, seen_before, seen_before)).fetchall()
        return [self._link(r[:-1]) for r in rows]

    def get(self, link_id: int) -> StoredLink | None:
        row = self._conn.execute(f"SELECT {_COLUMNS} FROM company_links WHERE id = ?", (link_id,)).fetchone()
        return self._link(row) if row else None

    def delete_pair(self, a: Collection[str], b: Collection[str], now: str) -> None:
        """Every link between the two companies (given as their listings), either way round."""
        where, args = _pair(a, b)
        with transaction(self._conn):
            self._conn.execute(f"UPDATE company_links SET deleted_at = ? WHERE deleted_at IS NULL AND {where}",
                               (now, *args))

    def deleted(self, a: Collection[str], b: Collection[str]) -> bool:
        """Whether the user deleted a link between the two companies."""
        where, args = _pair(a, b)
        return self._conn.execute(f"SELECT 1 FROM company_links WHERE deleted_at IS NOT NULL AND {where} LIMIT 1",
                                  args).fetchone() is not None

    def _link(self, row) -> StoredLink:
        evidence = self._conn.execute(
            "SELECT a.id, a.title, a.url, a.outlet, a.published_at FROM company_link_evidence e "
            "JOIN articles a ON a.id = e.article_id WHERE e.link_id = ? ORDER BY a.id", (row[0],))
        return StoredLink(*row, evidence=tuple(DiscoveryEvidence(*r) for r in evidence))


def _pair(a: Collection[str], b: Collection[str]) -> tuple[str, tuple[str, ...]]:
    ma, mb = ", ".join("?" * len(a)), ", ".join("?" * len(b))
    return (f"((symbol IN ({ma}) AND other IN ({mb})) OR (symbol IN ({mb}) AND other IN ({ma})))",
            (*a, *b, *b, *a))
