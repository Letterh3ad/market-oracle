"""Notes on one article: a small model call that pulls out up to three facts bearing on the
share price, or says the article is not about the company. One article per call, so a 4B
model reads a single text it can hold."""

from __future__ import annotations

from dataclasses import dataclass

from oracle.research.budget import budget, cut
from oracle.research.citations import strip_citations
from oracle.research.llm import ChatModel

MAX_FACTS = 3
MAX_FACT = 200
# A small model reads the start of a long text best, and an article's news is in its start.
MAX_NOTE_TEXT = 12_000
CUT = " [cut]"

_SYSTEM = ("Role: note_taker. You read one article for an investor in {company}. List up to {n} short facts "
           "from it that could move the share price soon: results, guidance, deals, products, lawsuits, "
           "regulation, leadership, with their numbers and dates. Write the facts in English, whatever the "
           "article's language. Use only the article. If it is not about "
           '{company}, set "about_company" to false and give no facts. Reply with JSON: "about_company" and '
           '"facts".')
SCHEMA = {"type": "object",
          "properties": {"about_company": {"type": "boolean"},
                         "facts": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_FACTS}},
          "required": ["about_company", "facts"]}


@dataclass(frozen=True)
class Note:
    about: bool
    """False when the article is not about the company; it then has no facts."""
    facts: tuple[str, ...]


def system_prompt(company: str) -> str:
    return _SYSTEM.format(company=company, n=MAX_FACTS)


def note_room(context_tokens: int, system: str, heading: str) -> int:
    """Characters of article text that fit beside the prompt, its heading and the reply."""
    return max(0, min(MAX_NOTE_TEXT, budget(context_tokens, system) - len(heading) - 2))


async def take_note(model: ChatModel, company: str, heading: str, text: str, context_tokens: int) -> Note | None:
    """None when the reply is unusable; ModelError when the backend cannot answer at all."""
    system = system_prompt(company)
    room = note_room(context_tokens, system, heading)
    body = text if len(text) <= room else text[: max(0, room - len(CUT))] + CUT
    reply = await model.reply([{"role": "system", "content": system},
                               {"role": "user", "content": f"{heading}\n\n{body}"}], SCHEMA)
    return read_note(reply)


def read_note(reply: object) -> Note | None:
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
        if text and text not in kept:
            kept.append(text)
    return Note(True, tuple(kept[:MAX_FACTS]))
