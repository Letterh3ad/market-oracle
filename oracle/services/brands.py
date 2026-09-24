"""Cheap signals on which company a name in the news means when the universe alone cannot say:
the lines worth matching at all, a group's own listing among its companies, a parent the news
names, and news calling the company a startup."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from oracle.services.industry_words import word_list
from oracle.store.universe import SearchHit
from oracle.universe.listings import LEGAL_FORMS, blind_name, is_receipt, is_receipt_symbol, normalise_name
from oracle.universe.regions import countries_of, place_of

# After a group's name these leave it the group itself: "Siemens Aktiengesellschaft", "Sony Group Corporation".
GROUP_WORDS = LEGAL_FORMS | {"aktiengesellschaft", "group", "holding", "holdings", "company", "international",
                             "global", "publ"}
# In a listing's name these say nothing of which company it is.
PLAIN_WORDS = GROUP_WORDS | {"technology", "tech", "system", "solution", "enterprise", "industry", "partner",
                             "worldwide", "the", "and", "of", "class", "a", "b", "ordinary", "share", "common",
                             "stock", "new", "registered", "llc", "lp", "as"}
# Words the news uses of a company that no exchange lists.
_PRIVATE = re.compile(r"\b(?:start-?ups?|privately[- ](?:held|owned)|private company|series [a-h]\b|seed round|"
                      r"pre-seed|unicorn|venture-backed|vc-backed|funding round)|\brais(?:es|ed|ing)\b[^.]*\bround\b",
                      re.IGNORECASE)
_OWNED = r"(?:unit|subsidiary|division|arm|business|lab|venture)"
# A parent as the news writes it: one to three capitalised words.
_PARENT = r"([A-Z][\w&\-]*(?:\s+[A-Z][\w&\-]*){0,2})"
_POSSESSIVE = r"['’]s"


def usable(hits: Iterable[SearchHit]) -> list[SearchHit]:
    """Hits that can be a company's own shares: no depositary receipts (they repeat a company
    listed elsewhere, "Meta, Inc." in Sao Paulo), and no lines with no sector, size or country
    (bonds and certificates filed as equity, "BESI 8,5%PLD")."""
    return [h for h in hits if not is_receipt(h.name) and not is_receipt_symbol(h.symbol)
            and (h.sector or h.market_cap or h.country)]


def sized(hits: list[SearchHit]) -> list[SearchHit]:
    """The sized hits when any is: an unsized one is a regional quote the universe keeps as a
    company of its own ("YASKAWA EL. CORP." in Duesseldorf)."""
    return [h for h in hits if h.market_cap] or hits


def behind_quotes(want: str, leading: list[SearchHit], hits: Sequence[SearchHit]) -> list[SearchHit]:
    """When only unsized quotes start with the name, the one sized company of their country whose
    name holds it ("Telefonaktiebolaget LM Ericsson" behind "ERICSSON B (FRIA)")."""
    if not leading or any(h.market_cap for h in leading):
        return leading
    countries = {h.country for h in leading}
    inside = [h for h in hits if h.market_cap and h.country in countries and f" {want} " in f" {blind_name(h.name)} "]
    return inside if len(inside) == 1 else leading


def the_group(want: str, hits: Sequence[SearchHit]) -> list[SearchHit]:
    """Hits whose name is the group's name and nothing else of note."""
    size = len(want.split())
    return [h for h in hits if (words := normalise_name(h.name).split())[:size] == want.split()
            and set(words[size:]) <= GROUP_WORDS]


def named_whole(want: str, hits: Sequence[SearchHit], items: Sequence[str]) -> list[SearchHit]:
    """Hits whose longer name the items write out ("Samsung SDI" for "Samsung")."""
    text = f" {' '.join(normalise_name(i) for i in items)} "
    return [h for h in hits if (b := blind_name(h.name)) != want and f" {b} " in text]


def names_in(name: str, items: Sequence[str]) -> bool:
    """The items write the name out whole."""
    text = f" {' '.join(normalise_name(i) for i in items)} "
    return f" {blind_name(name)} " in text


def other_words(listed: str, name: str) -> list[str]:
    """The words of a listing's name past those matched, bar ones naming no trade or place."""
    rest = word_list(normalise_name(listed))[len(word_list(normalise_name(name))):]
    return [w for w in rest if w not in PLAIN_WORDS and len(w) > 1 and not is_place(w) and not w.isdigit()]


def sounds_private(items: Sequence[str]) -> bool:
    return any(_PRIVATE.search(i) for i in items)


def parents(name: str, items: Sequence[str]) -> list[str]:
    """Companies the items say own `name`: "Alphabet's Intrinsic", "Google's robotics unit
    Intrinsic", "Alphabet-owned Intrinsic", "Intrinsic, an Alphabet company", "Intrinsic, part of
    Alphabet". A place is no owner ("Europe's Olix"), nor is a rival ("Alphabet's rival Intrinsic")."""
    n = re.escape(name)
    patterns = [
        rf"{_PARENT}{_POSSESSIVE}\s+(?:(?:[a-z][\w\-]*\s+){{0,2}}{_OWNED}\s+)?{n}\b",
        rf"{_PARENT}-(?:owned|controlled)\s+{n}\b",
        rf"{_PARENT}\s+{_OWNED}\s+{n}\b",
        rf"\b{n},?\s+(?i:an?|the)\s+{_PARENT}\s+(?i:company|unit|subsidiary|division|business)\b",
        rf"\b{n},?\s+(?i:(?:which\s+is\s+)?(?:part|a\s+unit|a\s+subsidiary|a\s+division)\s+of|owned\s+by)\s+{_PARENT}",
        rf"\b{n},?\s+{_PARENT}{_POSSESSIVE}\s+(?:[a-z][\w\-]*\s+){{0,2}}(?:{_OWNED}|company)\b",
    ]
    found: list[str] = []
    for item in items:
        for pattern in patterns:
            for m in re.finditer(pattern, item):
                # A title's capitals run on ("Why Alphabet's Intrinsic"): the run, then less of its start.
                words = m.group(1).split()
                for owner in (" ".join(words[k:]) for k in range(len(words))):
                    if owner.casefold() != name.casefold() and not is_place(owner) and owner not in found:
                        found.append(owner)
    return found


def is_place(text: str) -> bool:
    """A country, region or nationality word."""
    return countries_of(text) is not None or place_of(text) is not None
