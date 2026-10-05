"""Full texts of the literature index's sources and the "papers I'd like" list (research-lab 07), in the
research database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from oracle.store.db import transaction


@dataclass(frozen=True)
class FullTextRow:
    source_id: int
    via: str
    url: str | None
    licence: str
    version: str
    file: str | None
    pages: int
    chars: int
    at: str
    by: str


@dataclass(frozen=True)
class WantedRow:
    source_id: int
    reason: str
    note: str
    at: str
    by: str


_FULL = "source_id, via, url, licence, version, file, pages, length(text), at, by"


class PapersRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def keep_text(self, source_id: int, text: str, via: str, url: str | None, licence: str, version: str,
                  file: str | None, pages: int, at: str, by: str) -> None:
        """The text replaces any earlier one, and the source leaves the wanted list."""
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO lit_full_texts (source_id, text, via, url, licence, version, file, pages, at, by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (source_id, text, via, url, licence, version, file, pages, at, by))
            self._conn.execute("DELETE FROM lit_wanted WHERE source_id = ?", (source_id,))

    def full_text(self, source_id: int) -> FullTextRow | None:
        r = self._conn.execute(f"SELECT {_FULL} FROM lit_full_texts WHERE source_id = ?", (source_id,)).fetchone()
        return None if r is None else FullTextRow(*r)

    def origin(self, source_id: int) -> tuple[str, str] | None:
        """Where a source's full text came from and when, without measuring the text."""
        r = self._conn.execute("SELECT via, at FROM lit_full_texts WHERE source_id = ?", (source_id,)).fetchone()
        return None if r is None else (r[0], r[1])

    def text(self, source_id: int) -> str | None:
        r = self._conn.execute("SELECT text FROM lit_full_texts WHERE source_id = ?", (source_id,)).fetchone()
        return None if r is None else r[0]

    def full_texts(self) -> list[FullTextRow]:
        return [FullTextRow(*r) for r in self._conn.execute(f"SELECT {_FULL} FROM lit_full_texts ORDER BY source_id")]

    def want(self, source_id: int, reason: str, note: str, at: str, by: str) -> None:
        """A note already given survives a later lookup's reason."""
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO lit_wanted (source_id, reason, note, at, by) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (source_id) DO UPDATE SET reason = excluded.reason, at = excluded.at, by = excluded.by, "
                "note = CASE WHEN excluded.note = '' THEN lit_wanted.note ELSE excluded.note END",
                (source_id, reason, note, at, by))

    def unwant(self, source_id: int) -> bool:
        with transaction(self._conn):
            return self._conn.execute("DELETE FROM lit_wanted WHERE source_id = ?", (source_id,)).rowcount > 0

    def wanted(self) -> list[WantedRow]:
        return [WantedRow(*r) for r in self._conn.execute(
            "SELECT source_id, reason, note, at, by FROM lit_wanted ORDER BY at, source_id")]
