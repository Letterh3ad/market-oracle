"""Words of sectors and industries, as the universe names them and as people write them."""

from __future__ import annotations

import re

# Words that name no sector or industry ("oil and gas sector").
NOT_A_SECTOR = frozenset({"and", "or", "the", "of", "a", "an", "sector", "sectors", "industry", "industries",
                          "companies", "company", "stocks", "shares", "firms", "no", "not", "any"})
# Everyday words for what the universe names otherwise, after plurals are folded.
ALIASES = {"tech": "technology", "pharma": "pharmaceutical", "biotech": "biotechnology", "insurer": "insurance",
           "chip": "semiconductor", "chipmaker": "semiconductor", "healthcare": "health care",
           "automaker": "automobile", "car": "automobile", "cars": "automobile", "auto": "automobile",
           "autos": "automobile", "telecom": "telecommunication", "telecoms": "telecommunication",
           "drug": "pharmaceutical", "drugmaker": "pharmaceutical", "carmaker": "automobile"}
_WORDS = re.compile(r"[a-z0-9]+")


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
