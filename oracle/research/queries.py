"""Search queries for a company before the loop starts. A 4B model left alone writes
queries like "Apple Inc. recent news headlines 2026"; seeds built from the company's name
anchor it, and its own suggestions add what it knows is going on."""

from __future__ import annotations

import re

from oracle.research.llm import ChatModel

MAX_SUGGESTED = 4
MAX_QUERY = 100

# Trailing legal forms and holding words that no headline uses ("Sony Group Corporation"
# is "Sony" in the news).
_SUFFIX = re.compile(
    r"[\s,]+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|ag|sa|se|nv|n\.v|s\.a|s\.p\.a|spa|ab|"
    r"asa|oyj|a/s|kk|k\.k|p\.l\.c|pref|preferred|holdings?|group|\(publ\))\.?$",
    re.IGNORECASE,
)

# A word for "share price" where the listing's country reads another language, with the
# language named for the model.
LOCAL = {
    "Japan": ("株価", "Japanese"), "China": ("股价", "Chinese"), "Hong Kong": ("股价", "Chinese"),
    "Taiwan": ("股價", "Chinese"), "South Korea": ("주가", "Korean"), "Germany": ("Aktie", "German"),
    "Austria": ("Aktie", "German"), "Switzerland": ("Aktie", "German"), "France": ("action", "French"),
    "Belgium": ("action", "French"), "Italy": ("azioni", "Italian"), "Spain": ("acciones", "Spanish"),
    "Mexico": ("acciones", "Spanish"), "Netherlands": ("aandeel", "Dutch"), "Sweden": ("aktie", "Swedish"),
    "Denmark": ("aktie", "Danish"), "Norway": ("aksje", "Norwegian"), "Finland": ("osake", "Finnish"),
    "Brazil": ("ações", "Portuguese"), "Portugal": ("ações", "Portuguese"),
}

SUGGEST = """You plan news searches about a listed company for an investor holding for days to weeks.
Reply with JSON: "queries", a list of up to {cap} short search queries (under 8 words each) about
what could move its share price now: products, results, deals, lawsuits, leadership, regulation.
Use the company's everyday name. No dates, no words like "latest" or "news"."""


def short_name(name: str) -> str:
    while (shorter := _SUFFIX.sub("", name).strip(" ,")) != name and shorter:
        name = shorter
    return name


def seed_queries(name: str, country: str | None) -> list[str]:
    short = short_name(name)
    seeds = [f"{short} shares", f"{short} earnings outlook"]
    if country in LOCAL:
        seeds.append(f"{short} {LOCAL[country][0]}")
    return seeds


async def suggest(model: ChatModel, context: str, country: str | None) -> list[str]:
    """The model's own queries; raises ModelError only if the backend cannot answer."""
    ask = context
    if country in LOCAL:
        ask += f" Include one query with the company's name written in {LOCAL[country][1]}."
    reply = await model.reply(
        [{"role": "system", "content": SUGGEST.format(cap=MAX_SUGGESTED)}, {"role": "user", "content": ask}],
        {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
         "required": ["queries"]},
    )
    found = reply.get("queries") if isinstance(reply, dict) else None
    return [q for q in found if isinstance(q, str)] if isinstance(found, list) else []


def plan(seeds: list[str], suggested: list[str]) -> list[str]:
    """Seeds, then up to MAX_SUGGESTED new suggestions: blank, overlong and repeated
    (ignoring case) ones dropped."""
    out, seen, added = [], set(), 0
    for i, query in enumerate([*seeds, *suggested]):
        query = " ".join(query.split())
        key = query.casefold()
        if not query or len(query) > MAX_QUERY or key in seen:
            continue
        if i >= len(seeds):
            if added == MAX_SUGGESTED:
                break
            added += 1
        seen.add(key)
        out.append(query)
    return out
