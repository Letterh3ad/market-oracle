"""Words of sectors and industries, as the universe names them and as people write them."""

from __future__ import annotations

import re
from dataclasses import dataclass

from oracle.store.universe import CLASSIFICATION_LEVELS

# Words that name no sector or industry ("oil and gas sector").
NOT_A_SECTOR = frozenset({"and", "or", "the", "of", "a", "an", "sector", "sectors", "industry", "industries",
                          "companies", "company", "stocks", "shares", "firms", "no", "not", "any"})
# Everyday words for what the universe names otherwise, after plurals are folded.
ALIASES = {"tech": "technology", "pharma": "pharmaceutical", "biotech": "biotechnology", "insurer": "insurance",
           "chip": "semiconductor", "chipmaker": "semiconductor", "healthcare": "health care",
           "automaker": "automobile", "car": "automobile", "cars": "automobile", "auto": "automobile",
           "autos": "automobile", "telecom": "telecommunication", "telecoms": "telecommunication",
           "drug": "pharmaceutical", "drugmaker": "pharmaceutical", "carmaker": "automobile",
           "defence": "defense", "semiconducter": "semiconductor", "ev": "automobile",
           "homebuilder": "household durable", "miner": "mining", "battery": "electrical"}
_WORDS = re.compile(r"[a-z0-9]+")
# "Tech" alone means the sector, not Health Care Technology or Technology Hardware.
_WHOLE = {frozenset({"technology"}): frozenset({"information", "technology"})}


def words(text: str) -> set[str]:
    return set(word_list(text))


def word_list(text: str) -> list[str]:
    """Words in order, singular, without the ones that name no sector."""
    return [s for w in _WORDS.findall(text.casefold()) if (s := _singular(w)) not in NOT_A_SECTOR]


def universe_words(word: str) -> list[str]:
    """The universe's words for one written word."""
    return ALIASES.get(word, word).split()


def _singular(word: str) -> str:
    # Short words are left alone: "gas" is no plural.
    if len(word) <= 4:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


@dataclass(frozen=True)
class Classes:
    """Words as the universe's classifications at one level."""

    text: str
    level: str
    """A ListingProfile field: industry, industry_group or sector."""
    values: frozenset[str]

    def holds(self, p) -> str | None:
        """The company's classification these words name, or None."""
        value = getattr(p, self.level)
        return value if value in self.values else None


def classes(text: str, classifications: dict[str, frozenset[str]]) -> Classes | None:
    """The classifications holding every word of `text`, singular or plural and in everyday
    words ("utility" finds "Utilities", "chipmakers" finds "Semiconductors"); one shared word is
    not enough ("consumer staples" is not "Consumer Discretionary"). Only the most specific level
    is used, an exact name before one merely holding the words, so "biotech" is Biotechnology,
    not the Pharmaceuticals beside it in their combined group, and "media" is Media, not
    Interactive Media & Services. Short of an exact name, a sector or group whose name starts
    with the words ("consumer" is both Consumer sectors, "telecom" Telecommunication Services)
    comes before any name holding them somewhere. None when nothing matches."""
    wanted = frozenset(w for word in words(text) for w in universe_words(word)) - NOT_A_SECTOR
    wanted = _WHOLE.get(wanted, wanted)
    if not wanted:
        return None
    tries = ([(level, lambda v: words(v) == wanted) for level in CLASSIFICATION_LEVELS]
             + [(level, lambda v: set(word_list(v)[:len(wanted)]) == wanted)
                for level in reversed(CLASSIFICATION_LEVELS[1:])]
             + [(level, lambda v: wanted <= words(v)) for level in CLASSIFICATION_LEVELS])
    for level, matches in tries:
        if found := frozenset(v for v in classifications.get(level, ()) if matches(v)):
            return Classes(text, level, found)
    return None
