"""Companies named in a batch of headlines, found by one small bounded model call. The
model only names; turning a name into a listing is the caller's code."""

from __future__ import annotations

import re
from dataclasses import dataclass

from oracle.research.citations import strip_citations
from oracle.universe.listings import blind_name, normalise_name

RELATIONS = ("supplier", "customer", "competitor", "partner", "other")
LINKS = RELATIONS[:-1]
"""Relations a news item can state between two companies it names."""
MAX_COMPANIES = 8
MAX_NAME = 80
MAX_WHY = 200
# The numbers of the items a line rests on travel with it; written into the line they are noise.
# Only numbers of items given are taken for such, so "(2025)" stays.
_ITEM_REFS = re.compile(r"\s*[\[(]\s*(\d+(?:\s*,\s*\d+)*)\s*[\])]")


def _schema(relation: bool, why: bool = False, link: bool = False) -> dict:
    item = {"name": {"type": "string", "maxLength": MAX_NAME},
            "headlines": {"type": "array", "maxItems": 5, "items": {"type": "integer"}}}
    if relation:
        item["relation"] = {"type": "string", "enum": list(RELATIONS)}
    if why:
        item["why"] = {"type": "string", "maxLength": MAX_WHY}
    # Optional: most answers state no link between two companies.
    optional = {"related_to": {"type": "string", "maxLength": MAX_NAME},
                "relation": {"type": "string", "enum": list(LINKS)}} if link else {}
    return {
        "type": "object",
        "properties": {"companies": {"type": "array", "maxItems": MAX_COMPANIES, "items": {
            "type": "object", "properties": item | optional, "required": list(item)}}},
        "required": ["companies"],
    }


MENTIONS_SCHEMA = _schema(relation=True)
TOPIC_SCHEMA = _schema(relation=False)
"""For headlines about a topic, not a company: nothing to relate to."""
QUESTION_SCHEMA = _schema(relation=False, why=True, link=True)
"""For news items found for a question: each company with one line on why it answers it, and
another company an item states it supplies, buys from, competes or partners with, if any."""


@dataclass(frozen=True)
class Mention:
    name: str
    relation: str
    headlines: tuple[int, ...]
    """0-based positions in the headlines given."""
    why: str = ""
    """One line on why, where the schema asks for it."""
    related_to: str = ""
    """Another company a cited item names and states this one is `relation` of; empty for none."""


def mentions_prompt(seed: str, headlines: list[str]) -> list[dict]:
    system = (f"You read news headlines about {seed}. List the other companies the headlines name, "
              f"at most {MAX_COMPANIES}: each company's usual name as written in the headline, how it relates "
              f"to {seed} (supplier, customer, competitor, partner, or other), and the numbers of the "
              "headlines that name it. Leave out people, places, regulators, funds, indexes and "
              f"{seed} itself. Name only companies that a headline names; an empty list is a fine answer.")
    return _messages(system, headlines)


def topic_prompt(topic: str, headlines: list[str]) -> list[dict]:
    system = (f"You read news headlines found by searching for: {topic}. List the companies the headlines are "
              f"about, at most {MAX_COMPANIES}: each company's usual name as written in the headline, and the "
              "numbers of the headlines that name it. Leave out people, places, regulators, funds, indexes and "
              "news outlets. Name only companies that a headline names; an empty list is a fine answer.")
    return _messages(system, headlines)


def question_prompt(question: str, items: list[str]) -> list[dict]:
    system = (f"You answer an investor's question from numbered news items. The question: {question}\n"
              f"List the listed companies the items show answer it, best first, at most {MAX_COMPANIES}: each "
              "company's usual name as written in an item, one short line on why it fits (from the items only), "
              "and the numbers of the items that show it. When one of those items says the company is a "
              "supplier, customer, competitor or partner of another company it names, also give that "
              "company's name as \"related_to\" and the \"relation\"; otherwise leave both out. Leave out people, "
              "places, regulators, funds, indexes and news outlets. Name only companies that an item names; an "
              "empty list is a fine answer.")
    return _messages(system, items, "News items")


def _messages(system: str, headlines: list[str], heading: str = "Headlines") -> list[dict]:
    numbered = "\n".join(f"[{i}] {h}" for i, h in enumerate(headlines, start=1))
    return [{"role": "system", "content": system}, {"role": "user", "content": f"{heading}:\n{numbered}"}]


def parse_mentions(reply: object, headlines: list[str], seed: str | None = None) -> list[Mention]:
    """Well-formed mentions only, each named in a headline it cites; anything else is dropped."""
    items = reply.get("companies") if isinstance(reply, dict) else None
    if not isinstance(items, list):
        return []
    texts = [f" {normalise_name(h)} " for h in headlines]
    out: dict[str, Mention] = {}
    seed_key = blind_name(seed) if seed else None
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = " ".join(item["name"].split())[:MAX_NAME]
        key = blind_name(name)
        if not key or key in out or (seed_key and same_company(key, seed_key)):
            continue
        cited = item.get("headlines") if isinstance(item.get("headlines"), list) else []
        # 1-based in the prompt; a small model's out-of-range numbers are dropped, not trusted.
        at = tuple(dict.fromkeys(n - 1 for n in cited if isinstance(n, int) and not isinstance(n, bool)
                                 and 1 <= n <= len(headlines)))
        # A name no cited headline holds is the model's invention.
        at = tuple(i for i in at if f" {key} " in texts[i])
        if not at:
            continue
        relation = item.get("relation") if item.get("relation") in RELATIONS else "other"
        out[key] = Mention(name, relation, at, _why(item.get("why"), len(headlines)),
                           _related(item, key, [texts[i] for i in at]))
    return list(out.values())


def _related(item: dict, key: str, cited: list[str]) -> str:
    """The other company of a stated link, only when a cited item names it too."""
    other = item.get("related_to")
    if item.get("relation") not in LINKS or not isinstance(other, str):
        return ""
    other = " ".join(other.split())[:MAX_NAME]
    other_key = blind_name(other)
    if not other_key or same_company(other_key, key) or not any(f" {other_key} " in t for t in cited):
        return ""
    return other


def same_company(a: str, b: str) -> bool:
    """Blind names where one is the other's leading words ("toyota" and "toyota motor")."""
    return a == b or a.startswith(b + " ") or b.startswith(a + " ")


def _why(text: object, items: int) -> str:
    if not isinstance(text, str):
        return ""

    def refs(m: re.Match) -> str:
        return "" if all(1 <= int(n) <= items for n in m.group(1).split(",")) else m.group(0)

    return " ".join(_ITEM_REFS.sub(refs, strip_citations(text)).split())[:MAX_WHY]
