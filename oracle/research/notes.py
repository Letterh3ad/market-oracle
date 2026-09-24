"""Notes on one article: a small model call that pulls out up to three facts bearing on the
share price, or says the article is not about the company. One article per call, so a 4B
model reads a single text it can hold. Read for a question, the same call also keeps the
companies a list page names ("top 10", "List of ..."), as one more line of the note."""

from __future__ import annotations

from dataclasses import dataclass

from oracle.research.budget import REPLY_TOKENS, budget, cut, reply_room
from oracle.research.citations import strip_citations
from oracle.research.llm import ChatModel
from oracle.universe.listings import blind_name, normalise_name

MAX_FACTS = 3
MAX_FACT = 200
# A small model reads the start of a long text best, and an article's news is in its start.
MAX_NOTE_TEXT = 12_000
CUT = " [cut]"
MAX_LISTED = 15
# Asked of every article read for a question, a small model also "lists" the two companies a
# news story names; they are in its facts already, and that is no list page.
MIN_LISTED = 3
MAX_NAME = 80
LISTS = "Lists: "
"""Starts the note line holding a list page's companies."""

_SYSTEM = ("Role: note_taker. You read one article for an investor in {company}. List up to {n} short facts "
           "from it that could move the share price soon: results, guidance, deals, products, lawsuits, "
           "regulation, leadership, with their numbers and dates. Write the facts in English, whatever the "
           "article's language. Use only the article. If it is not about "
           '{company}, set "about_company" to false and give no facts. Reply with JSON: "about_company" and '
           '"facts".')
_LISTS = (' If the article lists or ranks companies of the kind asked about (a "top 10", a "List of" page, a '
          'table), it is about them: give in "companies" up to {n} of them that answer the question, each name '
          'as the article writes it; else none.').format(n=MAX_LISTED)
SCHEMA = {"type": "object",
          "properties": {"about_company": {"type": "boolean"},
                         "facts": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_FACTS}},
          "required": ["about_company", "facts"]}
LIST_SCHEMA = {"type": "object",
               "properties": SCHEMA["properties"] | {"companies": {
                   "type": "array", "items": {"type": "string", "maxLength": MAX_NAME}, "maxItems": MAX_LISTED}},
               "required": [*SCHEMA["required"], "companies"]}


@dataclass(frozen=True)
class Note:
    about: bool
    """False when the article is not about the company; it then has no facts."""
    facts: tuple[str, ...]


def system_prompt(company: str, lists: bool = False) -> str:
    return _SYSTEM.format(company=company, n=MAX_FACTS) + (_LISTS if lists else "")


def note_room(context_tokens: int, system: str, heading: str, reply_tokens: int = REPLY_TOKENS) -> int:
    """Characters of article text that fit beside the prompt, its heading and the reply."""
    return max(0, min(MAX_NOTE_TEXT, budget(context_tokens, system, reply_tokens) - len(heading) - 2))


async def take_note(model: ChatModel, company: str, heading: str, text: str, context_tokens: int,
                    lists: bool = False) -> Note | None:
    """None when the reply is unusable; ModelError when the backend cannot answer at all.
    `lists`: also keep the companies a list page names (reading for a question, not a company)."""
    system = system_prompt(company, lists)
    room = note_room(context_tokens, system, heading, reply_room(model))
    body = text if len(text) <= room else text[: max(0, room - len(CUT))] + CUT
    reply = await model.reply([{"role": "system", "content": system},
                               {"role": "user", "content": f"{heading}\n\n{body}"}], LIST_SCHEMA if lists else SCHEMA)
    return read_note(reply, body if lists else None)


def read_note(reply: object, listed_in: str | None = None) -> Note | None:
    """`listed_in`: the text the model read; a listed company it does not name is dropped."""
    if not isinstance(reply, dict):
        return None
    about, facts = reply.get("about_company"), reply.get("facts")
    if not isinstance(about, bool) or not isinstance(facts, list):
        return None
    if not about:
        return Note(False, ())
    kept: list[str] = []
    for fact in facts:
        # A fact is cited as its article; a page writing "[a12]" must not cite another.
        text = cut(strip_citations(fact).strip(), MAX_FACT) if isinstance(fact, str) else ""
        if text and text not in kept and not text.startswith(LISTS):
            kept.append(text)
    kept = kept[:MAX_FACTS]
    if listed_in is not None and len(names := _listed(reply.get("companies"), listed_in)) >= MIN_LISTED:
        kept.append(LISTS + "; ".join(names))
    return Note(True, tuple(kept))


def _listed(names: object, text: str) -> list[str]:
    """The names the text holds, in its own words, once each."""
    if not isinstance(names, list):
        return []
    words = f" {normalise_name(text)} "
    out: dict[str, str] = {}
    for name in names:
        name = " ".join(name.split())[:MAX_NAME] if isinstance(name, str) else ""
        key = blind_name(name)
        # The line is split on "; " when read back.
        if key and key not in out and f" {key} " in words and ";" not in name:
            out[key] = name
    return list(out.values())[:MAX_LISTED]


def listed(facts: tuple[str, ...]) -> tuple[str, ...]:
    """The companies a note's list line names."""
    line = next((f for f in facts if f.startswith(LISTS)), "")
    return tuple(line.removeprefix(LISTS).split("; ")) if line else ()
