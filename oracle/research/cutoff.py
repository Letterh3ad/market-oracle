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
KINDS = ("number", "name", "other")
"""A score, count or price; a person, team, company or country; a place, title or code name."""
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
    """Every one must be in the reply; "a|b" takes either, "58.x" any 58 or 58.<digits>."""
    source: str
    kind: str
    """One of KINDS: what a lucky guess would have to hit."""

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
        return self.right >= MIN_RIGHT


MIN_RIGHT = 2
"""Right answers that make a month known; every month has three or more questions, so one lucky
guess is never enough."""


def messages(e: Event) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": e.question}]


_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)


def reply_text(reply: object) -> str:
    """The reply's answer, without any reasoning a thinking model wrapped in <think> tags."""
    if isinstance(reply, dict) and isinstance(reply.get("answer"), str):
        text = reply["answer"]
    else:
        text = reply if isinstance(reply, str) else str(reply)
    return _THINKING.sub("", text).strip()


_DASHES = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2212]")
_FRACTION = re.compile(r"(\d+)(?:-| )?([13])/([24])(?!\d)")
_PERCENT = re.compile(r"%|\bper ?cent\b")
_DECIMAL = re.compile(r"(\d+)\.(\d+)")
_PUNCTUATION = re.compile(r"[^\w\s./-]")
_PAIR = re.compile(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)")
_ANY_DECIMALS = re.compile(r"(\d+)\.x")
_WHOLE_BEFORE = r"(?<![\w.])"
_WHOLE_AFTER = r"(?![\w]|\.\d)"


def normalize(text: str) -> str:
    """Lower case without accents, fractions as decimals without trailing zeros, and every
    punctuation mark but . - / as a space."""
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()
    text = _PERCENT.sub(" ", _DASHES.sub("-", text.replace("\u2044", "/")))
    text = _FRACTION.sub(lambda m: str(int(m[1]) + int(m[2]) / int(m[3])), text)
    text = _DECIMAL.sub(lambda m: m[1] + ("." + m[2].rstrip("0") if m[2].rstrip("0") else ""), text)
    return " ".join(_PUNCTUATION.sub(" ", text).replace("_", " ").split())


_HEDGE = re.compile(r"\b(?:unknown|not sure|unsure|no idea|do not know|don t know|dont know|cannot recall)\b")


def answered(reply: str, expect: Iterable[str]) -> bool:
    """Every expected keyword is there, and the reply does not hedge: "unknown, maybe X or Y"
    names X without knowing it."""
    said = normalize(reply)
    if _HEDGE.search(said):
        return False
    return all(any(_found(normalize(alt), said) for alt in item.split("|")) for item in expect)


def _found(keyword: str, said: str) -> bool:
    """Whole words and numbers only: "leo" is not in "leopold", "58" not in "58.17". A pair "a-b"
    (a range or a vote) is also found as "a to b" or "a and b", but never inside a longer chain
    like "3.75-4-4.25"."""
    if not keyword:
        return False
    if pair := _PAIR.fullmatch(keyword):
        lo, hi = map(re.escape, pair.groups())
        pattern = rf"(?<!\d-){lo}\s*(?:-|\bto\b|\band\b)\s*{hi}(?!-\d)"
    elif whole := _ANY_DECIMALS.fullmatch(keyword):
        pattern = rf"{whole[1]}(?:\.\d+)?"
    else:
        pattern = re.escape(keyword)
    return re.search(_WHOLE_BEFORE + pattern + _WHOLE_AFTER, said) is not None


def month_scores(marks: Iterable[tuple[date, bool]]) -> list[MonthScore]:
    """Right and asked per month, from each question's day and mark."""
    tally: dict[str, list[int]] = {}
    for day, right in marks:
        t = tally.setdefault(day.strftime("%Y-%m"), [0, 0])
        t[0] += right
        t[1] += 1
    return [MonthScore(m, r, n) for m, (r, n) in sorted(tally.items())]


def cutoff_from(scores: Iterable[MonthScore], claimed: date) -> date:
    """The end of the margin month after the last known month (MIN_RIGHT right), never
    before `claimed`. The last such month anywhere, not the first miss: a later lucky month
    only moves the cutoff later, the safe way for replays."""
    known = [s.month for s in scores if s.known]
    if not known:
        return claimed
    year, month = map(int, max(known).split("-"))
    year, month = divmod(year * 12 + month - 1 + MARGIN_MONTHS, 12)
    month += 1
    return max(claimed, date(year, month, calendar.monthrange(year, month)[1]))
