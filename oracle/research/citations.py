"""Findings cite evidence as [a<id>]. Only ids the run was actually shown count; a model
inventing one must not make a claim look sourced."""

from __future__ import annotations

import re

_CITE = re.compile(r"\s?\[a(\d+)\]")


def keep_citations(findings: str, shown: set[int]) -> tuple[str, set[int]]:
    """The findings with citations of anything not shown removed, and the ids kept."""
    kept: set[int] = set()

    def check(m: re.Match) -> str:
        article = int(m.group(1))
        if article in shown:
            kept.add(article)
            return m.group(0)
        return ""

    return _CITE.sub(check, findings), kept
