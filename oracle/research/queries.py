"""Search queries for a company before the loop starts. A 4B model left alone writes
queries like "Apple Inc. recent news headlines 2026"; seeds built from the company's name
anchor it, and its own suggestions add what it knows is going on."""

from __future__ import annotations

import re
from dataclasses import dataclass

from oracle.research.llm import ChatModel

MAX_SUGGESTED = 5
MAX_QUERY = 100
# Seeds are always kept; suggestions fill the rest. Each planned search costs a loop step, so
# the plan takes half of the default 16, leaving the rest for pages, filings and follow-ups.
MAX_PLANNED = 8

NEWS, LOCAL_NEWS, WEB = "news_search", "local_news_search", "web_search"

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

# Google News (hl, gl, ceid) for each LOCAL country, checked live: a pairing Google does not
# serve answers with a redirect. Denmark has no edition, and Google sends it to Norway's.
EDITION = {
    "Japan": ("ja", "JP", "JP:ja"), "China": ("zh-CN", "CN", "CN:zh-Hans"),
    "Hong Kong": ("zh-HK", "HK", "HK:zh-Hant"), "Taiwan": ("zh-TW", "TW", "TW:zh-Hant"),
    "South Korea": ("ko", "KR", "KR:ko"), "Germany": ("de", "DE", "DE:de"), "Austria": ("de", "AT", "AT:de"),
    "Switzerland": ("de", "CH", "CH:de"), "France": ("fr", "FR", "FR:fr"), "Belgium": ("fr", "BE", "BE:fr"),
    "Italy": ("it", "IT", "IT:it"), "Spain": ("es", "ES", "ES:es"), "Mexico": ("es-419", "MX", "MX:es-419"),
    "Netherlands": ("nl", "NL", "NL:nl"), "Sweden": ("sv", "SE", "SE:sv"), "Denmark": ("no", "NO", "NO:no"),
    "Norway": ("no", "NO", "NO:no"), "Finland": ("fi-FI", "FI", "FI:fi"),
    "Brazil": ("pt-BR", "BR", "BR:pt-419"), "Portugal": ("pt-PT", "PT", "PT:pt-150"),
}

SUGGEST = """You plan news searches about a listed company for an investor holding for days to weeks.
Reply with JSON: "queries", a list of up to {cap} short search queries (under 8 words each) about
what could move its share price now: products, results, deals, lawsuits, leadership, regulation.
Use the company's everyday name. No dates, no words like "latest" or "news"."""


def short_name(name: str) -> str:
    while (shorter := _SUFFIX.sub("", name).strip(" ,")) != name and shorter:
        name = shorter
    return name


@dataclass(frozen=True)
class Search:
    tool: str
    """The loop tool the search is planned for."""
    query: str


def seed_queries(name: str, country: str | None, web: bool) -> list[Search]:
    """`web` when the loop can search the web, for the company's own announcements."""
    short = short_name(name)
    seeds = [Search(NEWS, f"{short} shares"), Search(NEWS, f"{short} earnings outlook")]
    if country in LOCAL:
        seeds.append(Search(LOCAL_NEWS, f"{short} {LOCAL[country][0]}"))
    if web:
        seeds.append(Search(WEB, f"{short} press release investor relations"))
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


def plan(seeds: list[Search], suggested: list[str], country: str | None) -> list[Search]:
    """Seeds, then new suggestions up to MAX_SUGGESTED and MAX_PLANNED in all: blank, overlong
    and repeated (ignoring case) ones dropped. A suggestion outside the Latin alphabet goes to
    the local edition, which finds far more in that script than the English one."""
    out, seen, added = [], set(), 0
    local = country in EDITION
    for i, search in enumerate([*seeds, *(Search(NEWS, q) for q in suggested)]):
        query = " ".join(search.query.split())
        key = query.casefold()
        if not query or len(query) > MAX_QUERY or key in seen:
            continue
        if i >= len(seeds):
            if added == MAX_SUGGESTED or len(out) >= MAX_PLANNED:
                break
            added += 1
            search = Search(LOCAL_NEWS if local and not _latin(query) else NEWS, query)
        seen.add(key)
        out.append(Search(search.tool, query))
    return out


def _latin(text: str) -> bool:
    return all(ord(c) < 0x250 for c in text)
