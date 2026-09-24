"""Companies named in a batch of headlines, found by one small bounded model call. The
model only names; turning a name into a listing is the caller's code."""

from __future__ import annotations

from dataclasses import dataclass

from oracle.universe.listings import blind_name, normalise_name

RELATIONS = ("supplier", "customer", "competitor", "partner", "other")
MAX_COMPANIES = 8
MAX_NAME = 80

MENTIONS_SCHEMA = {
    "type": "object",
    "properties": {"companies": {"type": "array", "maxItems": MAX_COMPANIES, "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": MAX_NAME},
            "relation": {"type": "string", "enum": list(RELATIONS)},
            "headlines": {"type": "array", "maxItems": 5, "items": {"type": "integer"}},
        },
        "required": ["name", "relation", "headlines"],
    }}},
    "required": ["companies"],
}


@dataclass(frozen=True)
class Mention:
    name: str
    relation: str
    headlines: tuple[int, ...]
    """0-based positions in the headlines given."""


def mentions_prompt(seed: str, headlines: list[str]) -> list[dict]:
    numbered = "\n".join(f"[{i}] {h}" for i, h in enumerate(headlines, start=1))
    system = (f"You read news headlines about {seed}. List the other companies the headlines name, "
              f"at most {MAX_COMPANIES}: each company's usual name as written in the headline, how it relates "
              f"to {seed} (supplier, customer, competitor, partner, or other), and the numbers of the "
              "headlines that name it. Leave out people, places, regulators, funds, indexes and "
              f"{seed} itself. Name only companies that a headline names; an empty list is a fine answer.")
    return [{"role": "system", "content": system}, {"role": "user", "content": f"Headlines:\n{numbered}"}]


def parse_mentions(reply: object, headlines: list[str], seed: str) -> list[Mention]:
    """Well-formed mentions only, each named in a headline it cites; anything else is dropped."""
    items = reply.get("companies") if isinstance(reply, dict) else None
    if not isinstance(items, list):
        return []
    texts = [f" {normalise_name(h)} " for h in headlines]
    out: dict[str, Mention] = {}
    seed_key = blind_name(seed)
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = " ".join(item["name"].split())[:MAX_NAME]
        key = blind_name(name)
        if not key or key in out or same_company(key, seed_key):
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
        out[key] = Mention(name, relation, at)
    return list(out.values())


def same_company(a: str, b: str) -> bool:
    """Blind names where one is the other's leading words ("toyota" and "toyota motor")."""
    return a == b or a.startswith(b + " ") or b.startswith(a + " ")
