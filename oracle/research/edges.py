"""Candidate supplier / customer edges between companies: the universe companies a headline or a
filing's sentence names, and a keyword rule that types the tie without a model (the baseline
Laya's typing is measured against, and the fallback where it does no better).

Names are matched as discovery matches an exact name: legal forms aside (`blind_name`), plus a
company's first word where that word is distinctive and the company is clearly the biggest one
starting with it ("Toyota"). Common words and places are never names on their own."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from oracle.universe.listings import MARKET_CAP_BANDS, blind_name, normalise_name

EDGE_CHOICES = ("supplier", "customer", "competitor", "partner", "none")
"""What the other company is to the company, as a candidate is typed."""
MAX_WORDS = 6
MIN_SENTENCE = 20
MAX_SENTENCE = 600
"""Longer "sentences" are tables or lists run together."""
_POSSESSIVE = re.compile(r"['’]s\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n\s*\n")
_ABBREVIATION = re.compile(r"(?:\b(?:inc|co|corp|ltd|llc|plc|lp|jr|sr|dr|mr|ms|no|st|vs)|\b[a-z]\.[a-z])\.$", re.I)
_BULLET = re.compile(r"^(?:(?:and|or)\s+)?[\u2022\u25aa\u25cf\u00b7\u2013-]\s")
MAX_HEADING = 200
# The cover page (exchange listings, addresses), signatures and exhibit lists.
_COVER = re.compile(r"\bnasdaq (?:global select |global |capital )?market\b|\bstock market llc\b|"
                    r"\bnew york stock exchange\b|\btrading symbol|\bprincipal (?:executive|financial|accounting) "
                    r"(?:offices?|officer)\b|\bincorporated (?:herein )?by reference\b|/s/|\baggregate market value\b|"
                    r"\bperformance graphs?\b|\bnasdaq (?:\d+|composite)\b", re.I)
_FILLER_LEAD = ("the",)
_FILLER_TAIL = frozenset({"company", "companies", "group", "holdings", "holding", "incorporated", "the"})

# First words that are everyday English, places or generic business words: never a company on
# their own, though a whole name holding them ("Target Corporation" as "target") is refused too.
COMMON = frozenset("""
target visa block match next gap shell snap square unity sea arm global first general american united national
international standard royal new best one live life energy power capital group holdings company bank trust home
dollar public southern northern eastern western pacific atlantic central digital advanced applied data health
smart open alpha delta sigma omega apex summit pioneer progressive liberty freedom eagle phoenix focus core prime
premier elite select allied federal state city metro urban rural green blue red black white gold silver
star sun moon north south east west china japan taiwan korea india germany france britain british america
europe european asia asian africa african canada canadian australia australian mexico brazil hong kong singapore
swiss sweden norway denmark finland italy spain dutch russia saudi israel turkey london paris tokyo shanghai beijing
berlin zurich seoul texas california florida york jersey dallas boston chicago pacific world universal total
market markets trade value growth income fund funds index today news report reports shares stock stocks
giant bullish bearish trump corp fair president chairman chief john horizon chart news shift chinese japanese korean
indian german french italian spanish swedish norwegian danish finnish russian brazilian mexican indonesian thai
""".split())
FIRST_WORD_BANDS = ("Mega Cap", "Large Cap")
"""Only a big company is written by its first word alone ("Toyota"); a smaller one's first word
is more often someone's name or an everyday word."""
LOWERCASE_USES = 50
"""A word written in lower case in this many company descriptions or more is everyday English
("investor", "strategy", "iron"): `common_words` finds them, so a name made of it counts as common."""
_LOWER_WORD = re.compile(r"(?<![\w.])[a-z][a-z0-9]+(?!\w)")


def common_words(descriptions: Iterable[str], at_least: int = LOWERCASE_USES) -> frozenset[str]:
    """Words written in lower case in `at_least` of the descriptions."""
    counts: Counter[str] = Counter()
    for text in descriptions:
        counts.update(set(_LOWER_WORD.findall(text)))
    return frozenset(w for w, n in counts.items() if n >= at_least)


@dataclass(frozen=True)
class Listed:
    """A company the index can name: its primary symbol, name and market-cap band."""

    symbol: str
    name: str
    band: str | None = None


def words(text: str) -> list[str]:
    """Normalised words, possessives dropped, as `blind_name` spells names."""
    return [w for w in (normalise_name(t) for t in _POSSESSIVE.sub("", text).split()) if w]


def normal(text: str) -> str:
    return " ".join(words(text))


def _rank(band: str | None) -> int:
    return MARKET_CAP_BANDS.index(band) if band in MARKET_CAP_BANDS else len(MARKET_CAP_BANDS)


def aliases(name: str) -> set[str]:
    """The whole names a company is written by: legal forms, a leading "the" and a trailing
    "company" or "group" aside."""
    key = blind_name(name).split()
    while len(key) > 1 and key[0] in _FILLER_LEAD:
        key = key[1:]
    trimmed = list(key)
    while len(trimmed) > 1 and trimmed[-1] in _FILLER_TAIL:
        trimmed = trimmed[:-1]
    # "China Holdings" is never "China".
    if len(trimmed) == 1 and len(key) > 1 and trimmed[0] in COMMON:
        trimmed = key
    return {" ".join(k) for k in (key, trimmed) if k}


class NameIndex:
    """Universe companies by the names a headline or a filing writes them with."""

    def __init__(self, companies: Iterable[Listed], common: frozenset[str] = frozenset()) -> None:
        """`common`: everyday words beside COMMON (from `common_words`)."""
        self._common = COMMON | common
        whole: dict[str, list[Listed]] = {}
        first: dict[str, list[Listed]] = {}
        for c in companies:
            for key in aliases(c.name):
                whole.setdefault(key, []).append(c)
                if c.band in FIRST_WORD_BANDS:
                    first.setdefault(key.split()[0], []).append(c)
        self._names: dict[str, str] = {}
        for key, found in whole.items():
            if picked := _clearly_biggest(found):
                self._names[key] = picked.symbol
        for word, found in first.items():
            if word in self._names or len(word) < 4 or word in self._common or not word.isalpha():
                continue
            if picked := _clearly_biggest(found):
                self._names[word] = picked.symbol
        self._keys: dict[str, set[str]] = {}
        for key, symbol in self._names.items():
            self._keys.setdefault(symbol, set()).add(key)

    def keys_of(self, symbol: str) -> set[str]:
        return self._keys.get(symbol, set())

    def spans(self, text: str) -> list[tuple[str, int, int]]:
        """(symbol, first word, past last word) of each name, longest first, left to right."""
        raw = [t for t in _POSSESSIVE.sub("", text).split() if normalise_name(t)]
        ws = [normalise_name(t) for t in raw]
        out, i = [], 0
        while i < len(ws):
            for n in range(min(MAX_WORDS, len(ws) - i), 0, -1):
                key = " ".join(ws[i:i + n])
                symbol = self._names.get(key)
                if symbol and self._written(key, raw, i):
                    out.append((symbol, i, i + n))
                    i += n
                    break
            else:
                i += 1
        return out

    def _written(self, key: str, raw: list[str], i: int) -> bool:
        """A one-word name only capitalised, a short one only in capitals ("AMD", never "amd"), and
        never an everyday word ("Target", "Total": in filings mostly table headings and titles)."""
        if " " in key:
            return True
        if key in self._common:
            return False
        if len(key) < 3 or (len(key) < 4 and not key.isalpha()) or not raw[i][:1].isupper():
            return False
        return len(key) > 3 or raw[i].strip(".,:;()'\"").isupper()

    def find(self, text: str) -> set[str]:
        return {s for s, _, _ in self.spans(text)}


def _clearly_biggest(found: list[Listed]) -> Listed | None:
    """The only company, or the one a band bigger than every other; else none (ambiguous)."""
    unique = list({c.symbol: c for c in found}.values())
    if len(unique) == 1:
        return unique[0]
    ranked = sorted(unique, key=lambda c: _rank(c.band))
    return ranked[0] if _rank(ranked[0].band) < _rank(ranked[1].band) else None


def named_pairs(index: NameIndex, symbol: str, headline: str) -> set[str]:
    """The other companies a headline names beside the company, which it must name too."""
    found = index.find(headline)
    return found - {symbol} if symbol in found else set()


def sentences(text: str) -> list[str]:
    """A filing's sentences: an abbreviation ("Inc.") ends none, and a bullet carries the heading
    that introduced its list ("Our competitors include: • ..."), so it is read as the list's."""
    parts: list[str] = []
    for part in _SENTENCE.split(text):
        s = " ".join(part.split())
        if parts and _ABBREVIATION.search(parts[-1]):
            parts[-1] = f"{parts[-1]} {s}"
        elif s:
            parts.append(s)
    out, heading = [], None
    for s in parts:
        if _BULLET.match(s):
            s = f"{heading} {s}" if heading else s
        else:
            heading = s[:s.rindex(":") + 1] if ":" in s and s.rindex(":") < MAX_HEADING else None
        if MIN_SENTENCE <= len(s) <= MAX_SENTENCE and not _COVER.search(s):
            out.append(s)
    return out


def sentences_naming(index: NameIndex, filer: str, text: str) -> list[tuple[str, str]]:
    """(other company, sentence) for each company a filing's sentence names beside its filer,
    the first sentence naming it; in order of first appearance. The cover page, signatures and
    exhibit list name companies with no tie."""
    seen: dict[str, str] = {}
    for s in sentences(text):
        for other in index.find(s) - {filer}:
            seen.setdefault(other, s)
    return list(seen.items())


# The subject of a buying verb is the customer; of a supplying verb, the supplier.
_BUY = re.compile(r"\b(?:buys?|bought|orders?|ordered|purchases?|purchased|sources?|sourced)\b(?: \w+){0,6} from\b|"
                  r"\b(?:relies|rely|relying|depends?|depending|dependent) (?:up)?on\b|\b(?:taps|tapped|picks|picked|"
                  r"selects?|selected|chooses|chose)\b|\b(?:supplied|manufactured|made|provided|assembled) by\b")
_SUPPLY = re.compile(r"\b(?:supplies|supplying|to supply|will supply|supplier (?:to|of|for)|provides?|ships?|sells?|"
                     r"sold)\b(?: \w+){0,6} to\b|\b(?:supplies|supplying|to supply|will supply)\b|"
                     r"\bsupplier (?:to|of|for)\b|\bwins?\b(?: \w+){0,4} (?:orders?|contracts?|deals?)\b|"
                     r"\bsupply (?:deal|contract|agreement)\b")
# A bare "supplier" names its customer before and its supplier after ("Apple supplier Foxconn").
_SUPPLIER = re.compile(r"\bsuppliers?\b|\bfoundry\b|\bcontract manufacturers?\b|\boutsourcing partners?\b")
_CUSTOMER_OF = re.compile(r"\bcustomers? of\b|\bclients? of\b")
_CUSTOMER = re.compile(r"\bsales to\b|\brevenues? from\b|\baccounted for\b")
# A bare "customer" counts only this close to the other company's name.
_CUSTOMER_WORD = re.compile(r"\bcustomers?\b|\bclients?\b")
NEAR = 60
# "X vs Y" is mostly a which-stock-to-buy piece, not a business tie.
_COMPETITOR = re.compile(r"\bcompet\w*|\brivals?\b|\btakes on\b|\bbattles?\b|\bchallengers?\b")
# A competitor list's bullets say "suppliers of ..." about the competitors: read before the supply words.
_COMPETITOR_LIST = re.compile(r"\bcompetitors? (?:include|includes|including|are|such as)\b|\bour (?:\w+ )?competitors\b|"
                              r"\bcompet(?:e|es|ing) (?:\w+ )?(?:with|against)\b")
_PARTNER = re.compile(r"\bpartner\w*|\bteams? up\b|\bjoint venture\b|\bcollaborat\w*|\balliance\b|\bjoins? forces\b|"
                      r"\bagreements? with\b|\bdeals? with\b|\bties? up\b|\btieup\b")


def _at(text: str, names: str | Iterable[str]) -> int | None:
    keys = [names] if isinstance(names, str) else list(names)
    hits = []
    for name in keys:
        for key in {normal(name), *(normal(name).split()[:1])}:
            if key and (m := re.search(rf"\b{re.escape(key)}\b", text)):
                hits.append(m.start())
    return min(hits) if hits else None


def _role(company: int | None, other: int | None, at: int, subject: str, obj: str) -> str | None:
    """What the other company is, from which side of the verb at `at` it stands; the company
    unnamed (a filing's "we") stands as the subject."""
    if other is None:
        return None
    if other < at and (company is None or company > at):
        return subject
    if other > at and (company is None or company < at):
        return obj
    return None


def rule_type(text: str, company: str | Iterable[str], other: str | Iterable[str]) -> str:
    """What `other` is to `company` by keywords and word order; "none" when no keyword says."""
    t = normal(text)
    ca, co = _at(t, company), _at(t, other)
    if _COMPETITOR_LIST.search(t):
        return "competitor"
    for pattern, subject, obj in ((_BUY, "customer", "supplier"), (_SUPPLY, "supplier", "customer"),
                                  (_CUSTOMER_OF, "customer", "supplier"), (_SUPPLIER, "customer", "supplier")):
        for m in pattern.finditer(t):
            if role := _role(ca, co, m.start(), subject, obj):
                return role
    if _CUSTOMER.search(t) or (co is not None and any(abs(m.start() - co) <= NEAR for m in _CUSTOMER_WORD.finditer(t))):
        return "customer"
    if _COMPETITOR.search(t):
        return "competitor"
    if _PARTNER.search(t):
        return "partner"
    return "none"
