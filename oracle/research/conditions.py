"""Whether a verdict's "invalidated if" names a condition: a future event that would prove the
call wrong, not the evidence restated. A word check, so it costs no model call. Permissive:
one conditional or change word passes, since a real condition can be a bare noun phrase
("a guidance cut"), and so does a figure the reason does not give, told in no past tense
("Q3 revenue of $58B"), since a new figure is a threshold."""

from __future__ import annotations

import re

from oracle.research.citations import strip_citations

FACTS = "its condition names no future event, only facts"
REPEATS = "its condition repeats its reason"
EMPTY = "it gave no condition that would prove it wrong"

# Present-tense, noun and passive ("is delayed") forms: a restated fact is in the past tense
# ("declined", "grew"), so "declines" counts as a condition and "declined" does not.
_MARKERS = re.compile(r"\b(?:" + "|".join((
    r"if", r"when", r"whenever", r"unless", r"once", r"should", r"until", r"before", r"within", r"next",
    r"will", r"would", r"is not", r"isn't", r"does not", r"doesn't", r"do not", r"don't", r"no longer", r"never",
    r"below", r"under", r"above", r"beneath", r"less", r"fewer", r"lower", r"higher", r"short of", r"past",
    r"fails?", r"failure", r"falls?", r"drops?", r"declines?", r"miss(?:es|ed)?", r"cuts?", r"slows?", r"slowdown",
    r"stalls?", r"reverses?", r"reversal", r"weakens?", r"weaker", r"worse(?:ns)?", r"strengthens?", r"loses?",
    r"loss", r"downgrades?", r"rises?", r"sinks?", r"slips?", r"slides?", r"shrinks?", r"narrows?", r"widens?",
    r"fades?", r"softens?", r"turns?", r"breaks?", r"breach(?:es)?", r"rejects?", r"rejection", r"recalls?",
    r"delays?", r"delayed", r"halts?", r"halted", r"suspends?", r"suspended", r"blocks?", r"blocked",
    r"cancels?", r"cancell?ed", r"cancell?ation", r"withdraws?", r"withdrawn", r"denied", r"revoked",
    r"downgraded", r"rejected", r"lost", r"defaults?", r"probe", r"fine", r"penalty", r"ban", r"bans",
    r"tariffs?", r"sanctions?", r"lawsuit", r"investigation", r"strike", r"outage", r"recession", r"crash(?:es)?",
    r"collapses?", r"shortfall", r"write-?down", r"impairment", r"bankruptcy", r"resigns?", r"resignation",
    r"departure", r"exits?", r"plunges?", r"tumbles?", r"reports", r"announces", r"increases?", r"greater",
    r"exceeds", r"approved", r"resolved", r"raises?", r"raised", r"beats", r"improves?", r"upgrades?", r"upgraded",
    r"recovers?", r"recovery", r"rebounds?", r"accelerates?", r"surges?", r"jumps?", r"expands?", r"wins?",
    r"flat", r"unchanged", r"stays?", r"remains?", r"disappoints?", r"disappointing", r"launch(?:es)?",
    r"longer", r"shorter", r"slower", r"faster", r"stronger", r"sooner", r"later", r"more than", r"at least",
    r"at most", r"worsen", r"lags?", r"trails?",
    r"underperforms?", r"outperforms?", r"erodes?", r"erosion", r"compress(?:es)?", r"contraction",
    r"stalling", r"slowing", r"falling", r"declining", r"shrinking", r"weakening", r"worsening", r"dropping",
    r"takes", r"comes in", r"reach(?:es)?", r"hits", r"tops", r"approaches?", r"doubles?", r"halves?",
)) + r")\b", re.IGNORECASE)
# A standalone number (not "Q3" or a citation id), with its thousands commas.
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_YEAR = re.compile(r"(?:19|20)\d\d")
# A figure told in the past tense is a result, not a threshold: "grew to $58B".
_PAST = re.compile(r"\b(?:was|were|had|grew|rose|fell|came|went|saw|hit|beat|\w+[^e\W]ed)\b", re.IGNORECASE)
_NOT_PAST = {"hundred"}
_SENTENCE = re.compile(r"(?<=[.!?;:])\s+")
# Shorter shared sentences are common phrases, not copies.
_MIN_WORDS = 4


def condition_problem(condition: str | None, reason: str | None) -> str | None:
    """Why `condition` is no condition, or None when it is one."""
    condition, reason = strip_citations(condition or "").strip(), reason or ""
    if not condition:
        return EMPTY
    if _has_sentence_of(reason, condition) or _has_sentence_of(condition, reason):
        return REPEATS
    return None if _MARKERS.search(condition) or _new_figure(condition, reason) else FACTS


def _new_figure(condition: str, reason: str) -> bool:
    if any(m.group().lower() not in _NOT_PAST for m in _PAST.finditer(condition)):
        return False
    return bool(_figures(condition) - _figures(reason))


def _figures(text: str) -> set[str]:
    numbers = (n.replace(",", "").rstrip(".") for n in _NUMBER.findall(text))
    return {n for n in numbers if not _YEAR.fullmatch(n)}


def _has_sentence_of(source: str, text: str) -> bool:
    within = f" {_words(text)} "
    sentences = (_words(s) for s in _SENTENCE.split(source))
    return any(len(s.split()) >= _MIN_WORDS and f" {s} " in within for s in sentences)


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9%$]+", strip_citations(text).lower()))
