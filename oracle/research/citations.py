"""Findings cite evidence as [a<id>]. Only ids the run was actually shown count; a model
inventing one must not make a claim look sourced."""

from __future__ import annotations

import re

# Small models write (a3) and [a3, a7] as often as [a3]; all are read, and kept as [a3] [a7].
_CITE = re.compile(r"(\s?)[\[(]\s*(a\d+(?:\s*,\s*a\d+)*)\s*[\])]")


def keep_citations(findings: str, shown: set[int]) -> tuple[str, set[int]]:
    """The findings with citations of anything not shown removed, and the ids kept."""
    kept: set[int] = set()

    def check(m: re.Match) -> str:
        ids = [int(a.strip()[1:]) for a in m.group(2).split(",")]
        good = [i for i in ids if i in shown]
        kept.update(good)
        return f"{m.group(1)}{' '.join(f'[a{i}]' for i in good)}" if good else ""

    return _CITE.sub(check, findings), kept
