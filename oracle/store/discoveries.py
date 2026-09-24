from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass

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
class Check:
    """One check of an auto question's validation."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Validation:
    """What an auto question's validation made of one shortlisted company. Checks stop at the
    first that fails."""

    checks: tuple[Check, ...] = ()
    added: bool = False
    note: str | None = None
    """Why a company was not validated, or passed and still not added (a cap reached)."""

    @property
    def failed(self) -> Check | None:
        return next((c for c in self.checks if not c.passed), None)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str | None) -> Validation | None:
        if text is None:
            return None
        raw = json.loads(text)
        return cls(tuple(Check(**c) for c in raw["checks"]), raw["added"], raw["note"])


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
    question_id: int | None = None
    """The auto question that added it."""
    validation: Validation | None = None
    """The checks it passed to be added by an auto question."""


class DiscoveryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, symbol: str, company_key: str, name: str, source: str, seed: str, relation: str,
            evidence: Sequence[int], now: str, question_id: int | None = None,
            validation: Validation | None = None) -> None:
        """Put the listing on the discovered list with why, in one step."""
        with transaction(self._conn):
            WatchlistRepo(self._conn).add(DISCOVERED_LIST, symbol, now)
            cur = self._conn.execute(
                "INSERT INTO discoveries (symbol, company_key, name, source, seed, relation, added_at, question_id, "
                "validation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, company_key, name, source, seed, relation, now, question_id,
                 validation.to_json() if validation else None))
            self._conn.executemany("INSERT OR IGNORE INTO discovery_evidence VALUES (?, ?)",
                                   [(cur.lastrowid, a) for a in evidence])

    def latest(self, symbol: str) -> Discovery | None:
        row = self._conn.execute(
            "SELECT id, symbol, name, source, seed, relation, added_at, moved_at, question_id, validation "
            "FROM discoveries WHERE symbol = ? COLLATE NOCASE ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
        return None if row is None else Discovery(*row[1:8], evidence=self._evidence(row[0]), question_id=row[8],
                                                  validation=Validation.from_json(row[9]))

    def on_list_from(self, question_id: int) -> int:
        """Companies on the discovered list now that the question added."""
        return sum(1 for d in self.on_list() if d.question_id == question_id)

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

    def removed_rows(self) -> list[tuple[str, str | None, str | None]]:
        """(symbol, company key, name) of each removal."""
        return self._conn.execute("SELECT symbol, company_key, name FROM discovery_removed").fetchall()

    def removed(self) -> tuple[set[str], set[str]]:
        """(symbols, company keys) the user removed from the discovered list."""
        rows = self._conn.execute("SELECT symbol, company_key FROM discovery_removed").fetchall()
        return {r[0].upper() for r in rows}, {r[1] for r in rows if r[1]}

    def _evidence(self, discovery_id: int) -> tuple[DiscoveryEvidence, ...]:
        rows = self._conn.execute(
            "SELECT a.id, a.title, a.url, a.outlet, a.published_at FROM discovery_evidence e "
            "JOIN articles a ON a.id = e.article_id WHERE e.discovery_id = ? ORDER BY a.id", (discovery_id,))
        return tuple(DiscoveryEvidence(*r) for r in rows)
