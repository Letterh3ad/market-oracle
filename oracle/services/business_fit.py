"""Whether a company found only by the start of its name can be the one the news means, from
cheap signals. A sector the question does not ask about is never enough alone (headlines name
customers, partners and rivals of every trade, and the universe's sectors are sometimes wrong):
it takes a second sign as well, a small company or the rest of its name naming another trade
("Olix" in a chip question is not "OliX Pharmaceuticals"). The home the news gives the name
("German startup Olix") refuses on its own when the company is from elsewhere."""

from __future__ import annotations

import re
from dataclasses import dataclass

from oracle.services.industry_words import universe_words, word_list
from oracle.store.universe import ListingProfile, UniverseRepo
from oracle.universe.exchanges import EXCHANGES
from oracle.universe.listings import legal_form
from oracle.universe.regions import countries_of, place_of

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
# In a company's name these say little of its trade: "Micron Technology" makes chips.
_NAMES_ANY_TRADE = frozenset({"technology", "electronic", "media"})
SMALL = frozenset({"Small Cap", "Micro Cap", "Nano Cap"})
# Words that may stand between a home and the name it qualifies: "German defence group Rheinmetall".
_DESCRIBING = frozenset({
    "startup", "start", "up", "firm", "company", "group", "maker", "giant", "rival", "unicorn", "business",
    "supplier", "manufacturer", "champion", "tech", "ai", "fintech", "biotech", "chipmaker", "carmaker",
    "drugmaker", "defence", "defense", "chip", "software", "the", "a", "an",
})
# Legal forms a US company carries; any other on a US line is a foreign company's.
_US_FORMS = frozenset({"incorporated", "corporation", "co", "company", "llc", "lp", "trust"})
_TOKEN = re.compile(r"[a-z0-9]+(?:-based|'s)?|[,;:()]")
_RIGHT_QUOTE = chr(0x2019)


@dataclass(frozen=True)
class NamedIn:
    """What a name was found in: the question or theme (empty for a watched company's news,
    which asks nothing) and the items citing it."""

    asked: str = ""
    items: tuple[str, ...] = ()


class BusinessFit:
    def __init__(self, universe: UniverseRepo) -> None:
        self._universe = universe
        self._sectors: dict[str, frozenset[str]] | None = None

    def misfit(self, p: ListingProfile, name: str, named_in: NamedIn) -> str | None:
        """Why the company cannot be the one `name` means, or None when nothing says so."""
        homes = [h for item in named_in.items for h in _homes(item, name)]
        if homes and p.country and self._trusted_home(p) and not any(_holds(h, p.country) for h in homes):
            return f"a different company: {p.name} is from {p.country}, but the news names {', '.join(homes)}"
        asked = self._industries(named_in.asked)
        if not p.sector or not asked or any(p.sector in sectors for sectors in asked.values()):
            return None
        said = {w for text in (named_in.asked, *named_in.items) for word in word_list(text)
                for w in universe_words(word)}
        other_trade = [w for w in self._rest_of_name(p.name, name) if w not in said]
        if p.market_cap not in SMALL and not other_trade:
            return None
        size = f"a {p.market_cap} " if p.market_cap in SMALL else ""
        about = ", ".join(f'"{w}"' for w in list(asked)[:3])
        return (f"a different business: {p.name} is {size}in {p.industry or p.industry_group or p.sector}, "
                f"but the question is about {about}")

    def _rest_of_name(self, listed: str, name: str) -> list[str]:
        """Industry words in the listing's name after the words it was matched by."""
        rest = word_list(listed)[len(word_list(name)):]
        return [w for word in rest for w in universe_words(word)
                if w in self._vocabulary() and w not in _NAMES_ANY_TRADE]

    def _trusted_home(self, p: ListingProfile) -> bool:
        """Not a US line standing in for a foreign company (the universe often calls those US)."""
        if p.country != "United States":
            return True
        primary = self._universe.profile(p.primary_symbol)
        otc = EXCHANGES.get(primary.exchange if primary else None, ("", 0))[1] == 3
        foreign = bool(set(legal_form(p.name).split()) - _US_FORMS)
        return not (otc or foreign)

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
                for label in filter(None, (sector, *names)):
                    for w in word_list(label):
                        if w not in _TOO_COMMON:
                            by_word.setdefault(w, set()).add(sector)
            self._sectors = {w: frozenset(s) for w, s in by_word.items()}
        return self._sectors


def _holds(home: str, country: str) -> bool:
    return country in countries_of(home)


def _homes(text: str, name: str) -> list[str]:
    """The homes the text gives the name itself: a home just before it, with only describing
    words between ("German startup Olix", "UK-based Olix", "Europe's Olix"), or one after it past
    a comma ("Olix, the German startup")."""
    tokens = _tokens(text)
    want = _tokens(name)
    found: list[str] = []
    for at in range(len(tokens) - len(want) + 1):
        if tokens[at:at + len(want)] != want:
            continue
        before = at - 1
        while before >= 0 and tokens[before] in _DESCRIBING:
            before -= 1
        after = at + len(want)
        nearby = [tokens[before]] if before >= 0 else []
        if after < len(tokens) and tokens[after] == ",":
            after += 1
            while after < len(tokens) and tokens[after] in _DESCRIBING:
                after += 1
            nearby += tokens[after:after + 1]
        found += [h for t in nearby if (h := place_of(t)) and h not in found]
    return found


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold().replace(_RIGHT_QUOTE, "'"))
