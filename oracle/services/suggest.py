from __future__ import annotations

import difflib
from collections.abc import Iterable


def closest(key: str, candidates: Iterable[str]) -> list[str]:
    return difflib.get_close_matches(key, list(candidates), n=3, cutoff=0.6)


def hint(close: list[str], fallback: str) -> str:
    return f"did you mean {' or '.join(close)}?" if close else fallback
