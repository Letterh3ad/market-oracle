"""Open Source Asset Pricing's signals and long-short returns, the methods mapped to them, and the
funnel's stage runs (research-lab 05), in the research database."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import astuple, dataclass, fields

from oracle.store.db import transaction


@dataclass(frozen=True)
class OsapSignal:
    signal: str
    kind: str
    """Predictor, Placebo or Drop, as SignalDoc has it."""
    authors: str
    year: int | None
    journal: str
    description: str
    sample_start: int | None
    sample_end: int | None
    sign: float | None
    paper_return: float | None
    """The original paper's long-short return, percent a month."""
    paper_t: float | None
    weight: str
    quantile: float | None
    release: str


@dataclass(frozen=True)
class VariantCount:
    variant: str
    signals: int
    rows: int
    first: str | None
    last: str | None


@dataclass(frozen=True)
class ImportRow:
    what: str
    release: str
    file: str
    rows: int
    at: str


@dataclass(frozen=True)
class StageRow:
    id: int
    method_id: int
    stage: str
    outcome: str
    summary: str
    detail: dict
    status_from: str
    status_to: str
    by: str
    at: str


_SIGNAL = tuple(f.name for f in fields(OsapSignal))


class TriageRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def atomic(self):
        return transaction(self._conn)

    def replace_signals(self, rows: Iterable[OsapSignal]) -> int:
        rows = [astuple(r) for r in rows]
        with transaction(self._conn):
            self._conn.execute("DELETE FROM osap_signals")
            self._conn.executemany(f"INSERT INTO osap_signals ({', '.join(_SIGNAL)}) VALUES "
                                   f"({', '.join('?' * len(_SIGNAL))})", rows)
        return len(rows)

    def replace_returns(self, variant: str, rows: Iterable[tuple[str, str, float]]) -> int:
        """(signal, YYYY-MM, percent) rows replace every `variant` row."""
        with transaction(self._conn):
            self._conn.execute("DELETE FROM osap_returns WHERE variant = ?", (variant,))
            cur = self._conn.executemany("INSERT OR REPLACE INTO osap_returns (signal, variant, month, ret) "
                                         "VALUES (?, ?, ?, ?)", ((s, variant, m, r) for s, m, r in rows))
        return cur.rowcount

    def add_import(self, what: str, release: str, file: str, rows: int, at: str) -> None:
        with transaction(self._conn):
            self._conn.execute("INSERT INTO osap_imports (what, release, file, rows, at) VALUES (?, ?, ?, ?, ?)",
                               (what, release, file, rows, at))

    def imports(self, limit: int = 10) -> list[ImportRow]:
        return [ImportRow(*r) for r in self._conn.execute(
            "SELECT what, release, file, rows, at FROM osap_imports ORDER BY id DESC LIMIT ?", (limit,))]

    def signal(self, name: str) -> OsapSignal | None:
        r = self._conn.execute(f"SELECT {', '.join(_SIGNAL)} FROM osap_signals WHERE signal = ? COLLATE NOCASE",
                               (name,)).fetchone()
        return None if r is None else OsapSignal(*r)

    def signal_count(self) -> dict[str, int]:
        return dict(self._conn.execute("SELECT kind, COUNT(*) FROM osap_signals GROUP BY kind ORDER BY kind").fetchall())

    def counts(self) -> list[VariantCount]:
        return [VariantCount(*r) for r in self._conn.execute(
            "SELECT variant, COUNT(DISTINCT signal), COUNT(*), MIN(month), MAX(month) FROM osap_returns "
            "GROUP BY variant ORDER BY variant")]

    def returns(self, signal: str, variant: str) -> dict[str, float]:
        return dict(self._conn.execute("SELECT month, ret FROM osap_returns WHERE signal = ? AND variant = ?",
                                       (signal, variant)).fetchall())

    def set_map(self, method_id: int, signal: str | None, by: str, at: str) -> None:
        with transaction(self._conn):
            if signal is None:
                self._conn.execute("DELETE FROM lab_osap_map WHERE method_id = ?", (method_id,))
            else:
                self._conn.execute("INSERT OR REPLACE INTO lab_osap_map (method_id, signal, by, at) VALUES (?, ?, ?, ?)",
                                   (method_id, signal, by, at))

    def mapped(self) -> dict[int, str]:
        return dict(self._conn.execute("SELECT method_id, signal FROM lab_osap_map").fetchall())

    def record(self, method_id: int, stage: str, outcome: str, summary: str, detail: dict, status_from: str,
               status_to: str, by: str, at: str) -> None:
        """The stage run and the method's new status, together."""
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO lab_stages (method_id, stage, outcome, summary, detail, status_from, status_to, by, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (method_id, stage, outcome, summary, json.dumps(detail), status_from, status_to, by, at))
            self._conn.execute("UPDATE lit_methods SET status = ?, status_by = ?, status_at = ? WHERE id = ?",
                               (status_to, by, at, method_id))

    def stages(self, method_id: int | None = None) -> list[StageRow]:
        """Oldest first."""
        where, args = ("WHERE method_id = ?", (method_id,)) if method_id is not None else ("", ())
        return [StageRow(*r[:5], json.loads(r[5]), *r[6:]) for r in self._conn.execute(
            "SELECT id, method_id, stage, outcome, summary, detail, status_from, status_to, by, at FROM lab_stages "
            f"{where} ORDER BY id", args)]
