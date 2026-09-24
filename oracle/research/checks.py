"""Checks on a model's answer: whether a role's reply has the shape asked for, and which of
its claims survive. Every rejection is counted, so a garbled answer never passes silently."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Set

from oracle.research.trace import Claim, Drop, RoleCheck

_ID = re.compile(r"a?(\d+)")


class Answer:
    """One role's reply. It is usable only if every expected field is there with the
    expected type; an unusable reply reads as empty, and its claims are not counted."""

    def __init__(self, role: str, reply: object, expected: Mapping[str, type], shown: Set[int]) -> None:
        self.role = role
        self.usable = isinstance(reply, dict) and all(isinstance(reply.get(k), t) for k, t in expected.items())
        self._reply: dict = reply if self.usable else {}
        self._shown = shown
        self._dropped: Counter[Drop] = Counter()

    def get(self, name: str) -> object:
        return self._reply.get(name)

    def claims(self, name: str, limit: int) -> list[Claim]:
        items = self._reply.get(name)
        return [c for item in items if (c := self._claim(item, "text"))][:limit] if isinstance(items, list) else []

    def case(self, name: str) -> Claim | None:
        """The reply itself as one claim; a blank one with no citations means no case to make."""
        if not self.usable or (not str(self._reply.get(name)).strip() and not self._reply.get("cites")):
            return None
        return self._claim(self._reply, name)

    @property
    def check(self) -> RoleCheck:
        return RoleCheck(self.role, self.usable, dict(self._dropped))

    def _claim(self, item: object, text_field: str) -> Claim | None:
        claim, drop = _checked(item, text_field, self.role, self._shown)
        if drop:
            self._dropped[drop] += 1
        return claim


def _checked(item: object, text_field: str, role: str, shown: Set[int]) -> tuple[Claim | None, Drop | None]:
    text, cites = (item.get(text_field), item.get("cites")) if isinstance(item, dict) else (None, None)
    if not isinstance(text, str) or not isinstance(cites, list):
        return None, Drop.WRONG_SHAPE
    if not text.strip():
        return None, Drop.EMPTY_TEXT
    if not cites:
        return None, Drop.NO_CITATION
    ids: list[int] = []
    for cite in cites:
        m = _ID.fullmatch(str(cite).strip("[]() ").lower())
        if m and int(m.group(1)) in shown and int(m.group(1)) not in ids:
            ids.append(int(m.group(1)))
    return (Claim(text.strip(), tuple(ids), role), None) if ids else (None, Drop.UNKNOWN_CITATION)
