"""The research lab's test registry, in the research database."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import astuple, dataclass, fields

from oracle.store.db import transaction


@dataclass(frozen=True)
class Registration:
    key: str
    label: str
    kind: str
    """signal, event or strategy."""
    registered_on: str
    horizons: tuple[int, ...]
    rule: str
    universe: str
    expected_sign: str
    expected_size: str
    source: str
    power: str
    costs: str
    bar: float | None
    """The t the verdict needs, frozen at registration; None for a strategy (bar_rule judges it)."""
    bar_inclusive: bool
    """A t at the bar passes (lab tests); legacy ones needed t above it."""
    bar_rule: str
    kill_stage: str
    parent: str | None
    different_because: str | None
    commit_hash: str | None
    """The commit that put the registration in DECISIONS; no data run before it is set."""
    legacy: bool
    result_key: str
    note: str = ""
    variants: int = 1
    """Strategy versions tried before registering: the deflated Sharpe's trial count."""
    near_fail: str | None = None
    """The FAILed literature method its rule was near at registration, with the score (research-lab 04)."""
    near_check: str | None = None
    """How that check ran: meaning or keyword (Ollama down); None before research-lab 04."""
    near_check_note: str | None = None

    @property
    def hypotheses(self) -> int:
        """What it adds to N: each horizon once, a strategy once."""
        return max(1, len(self.horizons))


_COLUMNS = tuple(f.name for f in fields(Registration))
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM lab_tests"


def _row(r) -> Registration:
    d = dict(zip(_COLUMNS, tuple(r)))
    return Registration(**{**d, "horizons": tuple(json.loads(d["horizons"])), "bar_inclusive": bool(d["bar_inclusive"]),
                           "legacy": bool(d["legacy"])})


class LabTestRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def all(self) -> list[Registration]:
        """Oldest registration first, then by key."""
        return [_row(r) for r in self._conn.execute(f"{_SELECT} ORDER BY registered_on, key")]

    def get(self, key: str) -> Registration | None:
        r = self._conn.execute(f"{_SELECT} WHERE key = ?", (key,)).fetchone()
        return None if r is None else _row(r)

    def add(self, reg: Registration) -> None:
        values = list(astuple(reg))
        values[_COLUMNS.index("horizons")] = json.dumps(list(reg.horizons))
        with transaction(self._conn):
            self._conn.execute(f"INSERT INTO lab_tests ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                               values)

    def set_commit(self, key: str, commit_hash: str) -> None:
        with transaction(self._conn):
            self._conn.execute("UPDATE lab_tests SET commit_hash = ? WHERE key = ?", (commit_hash, key))

    def delete(self, key: str) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM lab_tests WHERE key = ?", (key,))
