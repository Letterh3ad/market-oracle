"""Literature methods' meaning vectors (research-lab 04), one per method and embedding model."""

from __future__ import annotations

import sqlite3
import sys
from array import array
from collections.abc import Iterable

from oracle.store.db import transaction


def _pack(vector: list[float]) -> bytes:
    a = array("f", vector)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def _unpack(blob: bytes) -> list[float]:
    a = array("f")
    a.frombytes(blob)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tolist()


class EmbeddingRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def all(self, model: str) -> dict[int, tuple[str, list[float]]]:
        """method id -> (text hash, vector), for `model`."""
        return {r[0]: (r[1], _unpack(r[2])) for r in self._conn.execute(
            "SELECT method_id, text_hash, vector FROM lit_embeddings WHERE model = ?", (model,))}

    def put(self, model: str, at: str, rows: Iterable[tuple[int, str, list[float]]]) -> None:
        """(method id, text hash, vector) each, replacing what the method had for `model`."""
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT OR REPLACE INTO lit_embeddings (method_id, model, text_hash, vector, at) VALUES (?, ?, ?, ?, ?)",
                [(mid, model, h, _pack(v), at) for mid, h, v in rows])
