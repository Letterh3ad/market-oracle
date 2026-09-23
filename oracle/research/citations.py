"""Findings cite evidence as [a<id>]. Only ids the run was actually shown count; a model
inventing one must not make a claim look sourced."""

from __future__ import annotations

import re

# Small models write (a3) as often as [a3]; both are read, and kept as [a3].
_CITE = re.compile(r"(\s?)[\[(]a(\d+)[\])]")


def keep_citations(findings: str, shown: set[int]) -> tuple[str, set[int]]:
    """The findings with citations of anything not shown removed, and the ids kept."""
    kept: set[int] = set()

    def check(m: re.Match) -> str:
        article = int(m.group(2))
        if article not in shown:
            return ""
        kept.add(article)
        return f"{m.group(1)}[a{article}]"

    return _CITE.sub(check, findings), kept
