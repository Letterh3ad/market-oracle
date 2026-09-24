"""Whether a company found only by the start of its name can be the one the news means, from
cheap signals: its sector against the industries the question and its news speak of, and when
those cannot tell, its home against the one the news gives it ("German startup Olix")."""

from __future__ import annotations

import re
from dataclasses import dataclass

from oracle.services.industry_words import universe_words, word_list
from oracle.store.universe import ListingProfile, UniverseRepo

# Classification words that news uses for anything ("financial results", "capital raise",
# "AI infrastructure", "energy storage"): matching on them would call any business a fit or a misfit.
_TOO_COMMON = frozenset({
    "air", "asset", "building", "capital", "care", "commercial", "communication", "component", "consumable",
    "consumer", "development", "direct", "distributor", "diversified", "electric", "energy", "engineering",
    "equipment", "equity", "finance", "financial", "good", "holding", "independent", "industrial", "information",
    "infrastructure", "instrument", "interactive", "internet", "investment", "it", "life", "management",
    "marketing", "market", "multi", "personal", "power", "producer", "product", "professional", "provider",
    "real", "road", "science", "service", "specialty", "storage", "supply", "tool", "trading", "trust", "water",
})
EUROPE = frozenset({
    "Austria", "Belgium", "Cyprus", "Czech Republic", "Denmark", "Estonia", "Finland", "France", "Germany",
    "Greece", "Guernsey", "Hungary", "Iceland", "Ireland", "Isle of Man", "Italy", "Jersey", "Latvia",
    "Lithuania", "Luxembourg", "Netherlands", "Norway", "Poland", "Portugal", "Spain", "Sweden", "Switzerland",
    "United Kingdom",
})
# How news gives a company its home: "German startup", "UK-based", "Europe's largest". A place
# merely named ("a plant in Mexico") says where something happens, not whose company it is.
_HOMES = {
    "Europe": ("european", "europe's"), "United States": ("american", "america's", "us-based"),
    "United Kingdom": ("british", "britain's", "uk", "uk's"), "Germany": ("german",), "France": ("french",),
    "Netherlands": ("dutch",), "Switzerland": ("swiss",), "Sweden": ("swedish",), "Finland": ("finnish",),
    "Norway": ("norwegian",), "Denmark": ("danish",), "Italy": ("italian",), "Spain": ("spanish",),
    "Ireland": ("irish",), "Belgium": ("belgian",), "Austria": ("austrian",), "Poland": ("polish",),
    "Japan": ("japanese",), "China": ("chinese",), "South Korea": ("korean", "south korean", "korea's"),
    "Taiwan": ("taiwanese",), "India": ("indian",), "Israel": ("israeli",), "Canada": ("canadian",),
    "Australia": ("australian",), "Brazil": ("brazilian",),
}
_RIGHT_QUOTE = chr(0x2019)
_OWN = re.compile(r"[a-z]+(?:-based|'s)?")


@dataclass(frozen=True)
class NamedIn:
    """What a name was found in: the question (or theme) and the items citing it."""

    asked: str = ""
    items: tuple[str, ...] = ()


class BusinessFit:
    def __init__(self, universe: UniverseRepo) -> None:
        self._universe = universe
        self._sectors: dict[str, frozenset[str]] | None = None

    def misfit(self, p: ListingProfile, named_in: NamedIn) -> str | None:
        """Why the company cannot be the one meant, or None when nothing says so."""
        spoken = self._industries(" ".join((named_in.asked, *named_in.items)))
        if p.sector and spoken:
            if any(p.sector in sectors for sectors in spoken.values()):
                return None
            about = ", ".join(f'"{w}"' for w in list(spoken)[:3])
            return (f"a different business: {p.name} is in {p.industry or p.industry_group or p.sector}, "
                    f"but the question and its news are about {about}")
        homes = _homes(" ".join(named_in.items))
        if p.country and homes and not any(p.country == h or p.country in EUROPE and h == "Europe" for h in homes):
            return f"a different company: {p.name} is from {p.country}, but the news names {', '.join(homes)}"
        return None

    def _industries(self, text: str) -> dict[str, frozenset[str]]:
        """Each written word naming an industry, with the sectors whose classifications hold it."""
        vocabulary = self._vocabulary()
        found: dict[str, frozenset[str]] = {}
        for word in word_list(text):
            sectors = frozenset(s for w in universe_words(word) for s in vocabulary.get(w, ()))
            if sectors:
                found.setdefault(word, sectors)
        return found

    def _vocabulary(self) -> dict[str, frozenset[str]]:
        if self._sectors is None:
            by_word: dict[str, set[str]] = {}
            for sector, *names in self._universe.classification_rows():
                if not sector:
                    continue
                for name in filter(None, (sector, *names)):
                    for w in word_list(name):
                        if w not in _TOO_COMMON:
                            by_word.setdefault(w, set()).add(sector)
            self._sectors = {w: frozenset(s) for w, s in by_word.items()}
        return self._sectors


def _homes(text: str) -> list[str]:
    """The homes the text gives a company, in `_HOMES` order."""
    tokens = _OWN.findall(text.casefold().replace(_RIGHT_QUOTE, "'"))
    seen = {*tokens, *(f"{a} {b}" for a, b in zip(tokens, tokens[1:])),
            *(t.removesuffix("-based") for t in tokens if t.endswith("-based") and t != "us-based")}
    return [home for home, forms in _HOMES.items() if seen.intersection(forms)]
