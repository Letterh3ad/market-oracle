"""Finding what a model actually knows: dated public events asked month by month, each reply
checked by keywords (no second model call), and the cutoff read off the monthly scores."""

from __future__ import annotations

import calendar
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
SYSTEM = ("You answer questions about dated public events from your own memory, with no searching. Give one short "
          "answer, a few words at most. If you do not know, or the date may be after what you learned, answer "
          "exactly \"unknown\". Never guess.")
MARGIN_MONTHS = 1
"""Months added after the last known one: a model knowing a little of a month may know more of the next."""


@dataclass(frozen=True)
class Event:
    id: str
    day: date
    question: str
    answer: str
    """The known answer, as a person would write it."""
    expect: tuple[str, ...]
    """Every one must be in the reply; "a|b" takes either."""
    source: str

    @property
    def month(self) -> str:
        return self.day.strftime("%Y-%m")


@dataclass(frozen=True)
class MonthScore:
    month: str
    right: int
    total: int

    @property
    def known(self) -> bool:
        return self.right * 2 >= self.total


def messages(e: Event) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": e.question}]


def reply_text(reply: object) -> str:
    if isinstance(reply, dict) and isinstance(reply.get("answer"), str):
        return reply["answer"]
    return reply if isinstance(reply, str) else str(reply)


_DASHES = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2212]")
_FRACTION = re.compile(r"(\d+)(?:-| )?([13])/([24])(?!\d)")
_PERCENT = re.compile(r"%|\bper ?cent\b")
_DECIMAL = re.compile(r"(\d+)\.(\d+)")
_RANGE = re.compile(r"(\d)\s*(?:-|\bto\b|\band\b)\s*(\d)")


def normalize(text: str) -> str:
    """Lower case without accents, fractions as decimals without trailing zeros, ranges as "a-b"."""
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()
    text = _PERCENT.sub(" ", _DASHES.sub("-", text.replace("\u2044", "/")))
    text = _FRACTION.sub(lambda m: str(int(m[1]) + int(m[2]) / int(m[3])), text)
    text = _DECIMAL.sub(lambda m: m[1] + ("." + m[2].rstrip("0") if m[2].rstrip("0") else ""), text)
    text = " ".join(text.split())
    return _RANGE.sub(r"\1-\2", text)


def answered(reply: str, expect: Iterable[str]) -> bool:
    said = normalize(reply)
    return all(any(_found(normalize(alt), said) for alt in item.split("|")) for item in expect)


def _found(keyword: str, said: str) -> bool:
    # Whole words and whole numbers only: "leo" is not in "leopold", nor "4-4.25" in "3.75-4-4.25".
    return bool(keyword) and re.search(rf"(?<![\w.])(?<!\d-){re.escape(keyword)}(?![\w]|\.\d|-\d)", said) is not None


def month_scores(marks: Iterable[tuple[date, bool]]) -> list[MonthScore]:
    """Right and asked per month, from each question's day and mark."""
    tally: dict[str, list[int]] = {}
    for day, right in marks:
        t = tally.setdefault(day.strftime("%Y-%m"), [0, 0])
        t[0] += right
        t[1] += 1
    return [MonthScore(m, r, n) for m, (r, n) in sorted(tally.items())]


def cutoff_from(scores: Iterable[MonthScore], claimed: date) -> date:
    """The end of the margin month after the last month answered at least half right, never
    before `claimed`. The last such month anywhere, not the first miss: a later lucky month
    only moves the cutoff later, the safe way for replays."""
    known = [s.month for s in scores if s.known]
    if not known:
        return claimed
    year, month = map(int, max(known).split("-"))
    year, month = divmod(year * 12 + month - 1 + MARGIN_MONTHS, 12)
    month += 1
    return max(claimed, date(year, month, calendar.monthrange(year, month)[1]))
