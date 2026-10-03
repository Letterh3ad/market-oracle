"""The literature index (research-lab 03) in the research database: sources and their ids, methods
and the sources they came from, go / drop / dig marks, and the searches that were run."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from oracle.store.db import transaction


@dataclass(frozen=True)
class SourceRow:
    id: int
    title: str
    authors: str
    year: int | None
    quality: str
    url: str | None
    read_on: str | None
    note: str
    added_at: str
    added_by: str
    also_titled: str
    """Another title the same paper goes by (e.g. its working-paper title), searched like the title."""


@dataclass(frozen=True)
class MethodRow:
    id: int
    ref: str
    name: str
    family: str
    definition: str
    aliases: tuple[str, ...]
    status: str
    duplicate_of: int | None
    origin: str
    rating: int | None
    archive_status: str
    archive_note: str
    evidence: str
    why: str
    result: str
    see_also: tuple[str, ...]
    test_keys: tuple[str, ...]
    """Tests in research-lab 01's registry, by key."""
    status_by: str
    status_at: str
    added_at: str


@dataclass(frozen=True)
class MarkRow:
    mark: str
    status_from: str
    status_to: str
    by: str
    at: str
    note: str


@dataclass(frozen=True)
class SearchRow:
    id: int
    query: str
    place: str
    at: str
    by: str
    note: str
    source_ids: tuple[int, ...]


_SOURCE = "id, title, authors, year, quality, url, read_on, note, added_at, added_by, also_titled"
_METHOD = ("id, ref, name, family, definition, aliases, status, duplicate_of, origin, rating, archive_status, "
           "archive_note, evidence, why, result, see_also, test_keys, status_by, status_at, added_at")
SOURCE_FIELDS = ("title", "authors", "year", "quality", "url", "read_on", "note")


def _method(r: sqlite3.Row) -> MethodRow:
    d = dict(r)
    return MethodRow(**{**d, **{k: tuple(json.loads(d[k])) for k in ("aliases", "see_also", "test_keys")}})


class LiteratureRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def atomic(self):
        """One transaction over several writes."""
        return transaction(self._conn)

    # Sources

    def add_source(self, title: str, authors: str, year: int | None, quality: str, url: str | None,
                   read_on: str | None, note: str, at: str, by: str, keys: Iterable[str] = (),
                   also_titled: str = "") -> int:
        with transaction(self._conn):
            new = self._conn.execute(
                "INSERT INTO lit_sources (title, authors, year, quality, url, read_on, note, added_at, added_by, "
                "also_titled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (title, authors, year, quality, url, read_on, note, at, by, also_titled)).lastrowid
            self.add_keys(new, keys)
        return new

    def add_keys(self, source_id: int, keys: Iterable[str]) -> None:
        """Keys another source already holds stay with it."""
        with transaction(self._conn):
            self._conn.executemany("INSERT OR IGNORE INTO lit_source_ids (key, source_id) VALUES (?, ?)",
                                   [(k, source_id) for k in keys])

    def update_source(self, source_id: int, **fields: object) -> None:
        bad = set(fields) - set(SOURCE_FIELDS)
        if bad:
            raise ValueError(f"not a source field: {sorted(bad)}")
        if fields:
            with transaction(self._conn):
                self._conn.execute(f"UPDATE lit_sources SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                                   (*fields.values(), source_id))

    def source(self, source_id: int) -> SourceRow | None:
        r = self._conn.execute(f"SELECT {_SOURCE} FROM lit_sources WHERE id = ?", (source_id,)).fetchone()
        return None if r is None else SourceRow(*r)

    def source_by_key(self, key: str) -> SourceRow | None:
        r = self._conn.execute(f"SELECT {', '.join('s.' + c for c in _SOURCE.split(', '))} FROM lit_sources s "
                               "JOIN lit_source_ids i ON i.source_id = s.id WHERE i.key = ?", (key,)).fetchone()
        return None if r is None else SourceRow(*r)

    def sources(self) -> list[SourceRow]:
        return [SourceRow(*r) for r in self._conn.execute(f"SELECT {_SOURCE} FROM lit_sources ORDER BY id")]

    def keys(self, source_id: int) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT key FROM lit_source_ids WHERE source_id = ? ORDER BY key",
                                                 (source_id,))]

    # Methods

    def add_method(self, ref: str, name: str, status: str, origin: str, at: str, by: str, *, family: str = "",
                   definition: str = "", aliases: Iterable[str] = (), rating: int | None = None,
                   archive_status: str = "", archive_note: str = "", evidence: str = "", why: str = "", result: str = "",
                   see_also: Iterable[str] = (), test_keys: Iterable[str] = ()) -> int:
        with transaction(self._conn):
            return self._conn.execute(
                "INSERT INTO lit_methods (ref, name, family, definition, aliases, status, origin, rating, archive_status, "
                "archive_note, evidence, why, result, see_also, test_keys, status_by, status_at, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ref, name, family, definition, json.dumps(list(aliases)), status, origin, rating, archive_status,
                 archive_note, evidence, why, result, json.dumps(list(see_also)), json.dumps(list(test_keys)), by, at, at)).lastrowid

    def method(self, ref: str) -> MethodRow | None:
        r = self._conn.execute(f"SELECT {_METHOD} FROM lit_methods WHERE ref = ? COLLATE NOCASE", (ref,)).fetchone()
        return None if r is None else _method(r)

    def method_by_id(self, method_id: int) -> MethodRow:
        return _method(self._conn.execute(f"SELECT {_METHOD} FROM lit_methods WHERE id = ?", (method_id,)).fetchone())

    def methods(self, status: str | None = None) -> list[MethodRow]:
        where, args = ("WHERE status = ?", (status,)) if status else ("", ())
        return [_method(r) for r in self._conn.execute(f"SELECT {_METHOD} FROM lit_methods {where} ORDER BY id", args)]

    def refs(self, prefix: str) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT ref FROM lit_methods WHERE ref LIKE ?", (f"{prefix}%",))]

    def set_status(self, method_id: int, mark: str, status_to: str, by: str, at: str, note: str) -> None:
        """The new status, and the mark that set it kept beside the old one."""
        with transaction(self._conn):
            old = self._conn.execute("SELECT status FROM lit_methods WHERE id = ?", (method_id,)).fetchone()[0]
            self._conn.execute("UPDATE lit_methods SET status = ?, status_by = ?, status_at = ? WHERE id = ?",
                               (status_to, by, at, method_id))
            self._conn.execute("INSERT INTO lit_marks (method_id, mark, status_from, status_to, by, at, note) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?)", (method_id, mark, old, status_to, by, at, note))

    def marks(self, method_id: int) -> list[MarkRow]:
        return [MarkRow(*r) for r in self._conn.execute(
            "SELECT mark, status_from, status_to, by, at, note FROM lit_marks WHERE method_id = ? ORDER BY id DESC",
            (method_id,))]

    def link(self, method_id: int, source_ids: Iterable[int]) -> None:
        with transaction(self._conn):
            self._conn.executemany("INSERT OR IGNORE INTO lit_method_sources (method_id, source_id) VALUES (?, ?)",
                                   [(method_id, s) for s in source_ids])

    def links(self) -> list[tuple[int, int]]:
        """Every (method id, source id)."""
        return [(r[0], r[1]) for r in self._conn.execute("SELECT method_id, source_id FROM lit_method_sources")]

    def method_sources(self, method_id: int) -> list[SourceRow]:
        return [SourceRow(*r) for r in self._conn.execute(
            f"SELECT {', '.join('s.' + c for c in _SOURCE.split(', '))} FROM lit_sources s JOIN lit_method_sources l "
            "ON l.source_id = s.id WHERE l.method_id = ? ORDER BY s.id", (method_id,))]

    def source_methods(self, source_id: int) -> list[MethodRow]:
        return [_method(r) for r in self._conn.execute(
            f"SELECT {', '.join('m.' + c for c in _METHOD.split(', '))} FROM lit_methods m JOIN lit_method_sources l "
            "ON l.method_id = m.id WHERE l.source_id = ? ORDER BY m.id", (source_id,))]

    # Searches

    def add_search(self, query: str, place: str, at: str, by: str, note: str, source_ids: Iterable[int]) -> int:
        with transaction(self._conn):
            new = self._conn.execute("INSERT INTO lit_searches (query, place, at, by, note) VALUES (?, ?, ?, ?, ?)",
                                     (query, place, at, by, note)).lastrowid
            self._conn.executemany("INSERT OR IGNORE INTO lit_search_sources (search_id, source_id) VALUES (?, ?)",
                                   [(new, s) for s in source_ids])
        return new

    def searches(self, limit: int) -> list[SearchRow]:
        rows = self._conn.execute("SELECT id, query, place, at, by, note FROM lit_searches ORDER BY id DESC LIMIT ?",
                                  (limit,)).fetchall()
        found: dict[int, list[int]] = {}
        for sid, src in self._conn.execute(
                f"SELECT search_id, source_id FROM lit_search_sources WHERE search_id IN ({','.join('?' * len(rows))}) "
                "ORDER BY source_id", [r[0] for r in rows]):
            found.setdefault(sid, []).append(src)
        return [SearchRow(*r, source_ids=tuple(found.get(r[0], ()))) for r in rows]
