from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from oracle.store.db import transaction


@dataclass(frozen=True)
class TagSummary:
    tag: str
    symbols: tuple[str, ...]
    """Primary symbol of each tagged company."""
    orphaned: tuple[str, ...]
    """Symbols tagged whose company has left the universe entirely."""


class TagRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def of_company(self, company_key: str) -> list[str]:
        rows = self._conn.execute(
            """SELECT DISTINCT t.tag FROM tags t JOIN listings x ON x.symbol = t.symbol
               WHERE x.company_key = ? ORDER BY t.tag""",
            (company_key,),
        )
        return [r[0] for r in rows]

    def of_companies(self, company_keys: list[str]) -> dict[str, tuple[str, ...]]:
        if not company_keys:
            return {}
        marks = ", ".join("?" for _ in company_keys)
        out: dict[str, set[str]] = {}
        for key, tag in self._conn.execute(
            f"""SELECT x.company_key, t.tag FROM tags t JOIN listings x ON x.symbol = t.symbol
                WHERE x.company_key IN ({marks})""",
            company_keys,
        ):
            out.setdefault(key, set()).add(tag)
        return {k: tuple(sorted(v)) for k, v in out.items()}

    def add(self, symbols: list[str], tag: str, via: str, created_at: str) -> None:
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT OR IGNORE INTO tags (symbol, tag, via, created_at) VALUES (?, ?, ?, ?)",
                [(s, tag, via, created_at) for s in symbols],
            )

    def of_symbol(self, symbol: str) -> list[str]:
        """Tags recorded against a symbol directly, whether or not it is still listed."""
        rows = self._conn.execute(
            """SELECT DISTINCT tag FROM tags
               WHERE symbol = :s COLLATE NOCASE OR via = :s COLLATE NOCASE ORDER BY tag""",
            {"s": symbol.strip()},
        )
        return [r[0] for r in rows]

    def remove(self, symbol: str, tag: str) -> bool:
        """Untag the company `symbol` belongs to, including rows from the same tagging
        whose listings have since left the universe."""
        with transaction(self._conn):
            cur = self._conn.execute(
                """DELETE FROM tags WHERE tag = :tag AND via IN (
                       SELECT via FROM tags WHERE tag = :tag AND (
                           symbol = :symbol COLLATE NOCASE
                           OR via = :symbol COLLATE NOCASE
                           OR symbol IN (
                               SELECT x.symbol FROM listings x WHERE x.company_key = (
                                   SELECT company_key FROM listings
                                   WHERE symbol = :symbol COLLATE NOCASE))))""",
                {"tag": tag, "symbol": symbol.strip()},
            )
            return cur.rowcount > 0

    def all_tags(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT DISTINCT tag FROM tags ORDER BY tag")]

    def summaries(self) -> list[TagSummary]:
        rows = self._conn.execute(
            """SELECT t.tag, t.via, p.symbol AS primary_symbol FROM tags t
               LEFT JOIN listings x ON x.symbol = t.symbol
               LEFT JOIN listings p ON p.company_key = x.company_key AND p.is_primary
               ORDER BY t.tag"""
        )
        by_tagging: dict[tuple[str, str], set[str]] = {}
        for tag, via, primary in rows:
            found = by_tagging.setdefault((tag, via), set())
            if primary is not None:
                found.add(primary)
        grouped: dict[str, tuple[set[str], set[str]]] = {}
        for (tag, via), primaries in by_tagging.items():
            symbols, orphaned = grouped.setdefault(tag, (set(), set()))
            if primaries:
                symbols.update(primaries)
            else:
                orphaned.add(via)
        return [
            TagSummary(tag, tuple(sorted(s)), tuple(sorted(o))) for tag, (s, o) in grouped.items()
        ]
