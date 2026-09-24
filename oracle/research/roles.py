"""The research team's roles, after TradingAgents' structure (Apache-2.0; structure
reimplemented, no text copied): three analysts read the evidence, a bull and a bear argue
from their findings, and a risk manager says what could go wrong. Prompts are short and
each role is one call, for a 4B model."""

from __future__ import annotations

from datetime import date

from oracle.research.budget import REPLY_TOKENS, budget
from oracle.research.checks import Answer
from oracle.research.citations import cited
from oracle.research.llm import ChatModel
from oracle.research.trace import Claim, Report

MAX_CLAIMS = 3
MAX_CITES = 5

_BASE = ("Role: {role}. You are {who} on a research team studying a listed company for an investor "
         "holding for days to a few weeks. {task} Use only the evidence given. Cite each claim by the "
         'ids of the evidence lines it rests on, like "a3". Leave out any claim you cannot cite.')
_CLAIMS = f'Reply with JSON: "findings", up to {MAX_CLAIMS} short claims, each with "text" and "cites".'

ANALYSTS = {
    "fundamental_analyst": ("the fundamental analyst",
                            f"Judge the business: valuation, profitability, growth, debt and filings. {_CLAIMS}"),
    "technical_analyst": ("the technical analyst",
                          f"Judge the price action and the analyst signals: trend, momentum, volatility. {_CLAIMS}"),
    "news_analyst": ("the news and sentiment analyst",
                     "Judge the news: results, products, deals, leadership, lawsuits, regulation, and how "
                     f"the market reads them. {_CLAIMS}"),
}
_CASE = 'Reply with JSON: "case" (two or three sentences) and "cites".'
SIDES = {
    "bull": ("the bull researcher", f"Make the strongest honest case for buying now, from the analysts' "
                                    f"findings and the evidence. {_CASE}"),
    "bear": ("the bear researcher", f"Make the strongest honest case against buying now, from the analysts' "
                                    f"findings and the evidence. {_CASE}"),
}
RISK = ("the risk manager", f'List up to {MAX_CLAIMS} risks that could hurt a holder soon, each with "text" and '
                            '"cites", and one plain condition that would prove the bull case wrong. Reply with '
                            'JSON: "risks" and "invalidated_if".')

# Bounded arrays: Ollama's grammar honours maxItems, so a small model cannot loop inside one.
_CITES = {"type": "array", "items": {"type": "string"}, "maxItems": MAX_CITES}
_CLAIM_LIST = {"type": "array", "maxItems": MAX_CLAIMS,
               "items": {"type": "object", "properties": {"text": {"type": "string"}, "cites": _CITES},
                         "required": ["text", "cites"]}}


# Room kept beside the evidence for what the later roles also read: the analysts' findings
# and the two cases.
LATER_ROLES_CHARS = 4000


def digest_room(context_tokens: int, reply_tokens: int = REPLY_TOKENS) -> int:
    """Characters of evidence digest every role can read beside its prompt."""
    longest = max((prompt(r, who, task) for r, (who, task) in {**ANALYSTS, **SIDES, "risk": RISK}.items()), key=len)
    return max(0, budget(context_tokens, longest, reply_tokens) - LATER_ROLES_CHARS)


def digest(statistics: str, read: list[str], headlines: list[str], notes: str, chars: int) -> str:
    """The evidence the roles read. The stored data and the researcher's notes are always
    there; then evidence with its facts, then headlines alone, strictly in that order until
    `chars` runs out."""
    head, tail = f"Evidence:\n{statistics}", f"\n\nResearcher's notes: {notes or '(none)'}"
    room = chars - len(head) - len(tail)
    kept: list[str] = []
    for line in (*read, *headlines):
        if len(line) + 1 > room:
            break
        kept.append(line)
        room -= len(line) + 1
    return "\n".join([head, *kept]) + tail


async def write_report(model: ChatModel, evidence: str, shown: set[int], day: date) -> Report:
    """Raises ModelError only if the backend cannot answer; a garbled role adds nothing,
    and the report's checks record it."""

    async def ask(role: str, who: str, task: str, material: str, schema: dict, expected: dict) -> Answer:
        reply = await model.reply([{"role": "system", "content": prompt(role, who, task)},
                                   {"role": "user", "content": material}],
                                  {"type": "object", "properties": schema, "required": list(schema)})
        answer = Answer(role, reply, expected, shown)
        answers.append(answer)
        return answer

    answers: list[Answer] = []
    findings: list[Claim] = []
    for role, (who, task) in ANALYSTS.items():
        answer = await ask(role, who, task, evidence, {"findings": _CLAIM_LIST}, {"findings": list})
        findings += answer.claims("findings", MAX_CLAIMS)
    material = f"{evidence}\n\nThe analysts' findings:\n{_listed(findings)}"
    sides = {}
    for role, (who, task) in SIDES.items():
        answer = await ask(role, who, task, material, {"case": {"type": "string"}, "cites": _CITES}, {"case": str})
        sides[role] = answer.case("case")
    cases = "\n".join(f"{c.role}: {c.text}" for c in sides.values() if c)
    risk = await ask("risk", *RISK, f"{material}\n\nThe cases:\n{cases}",
                     {"risks": _CLAIM_LIST, "invalidated_if": {"type": "string"}}, {"risks": list})
    condition = risk.get("invalidated_if")
    return Report(day, tuple(findings), sides["bull"], sides["bear"], tuple(risk.claims("risks", MAX_CLAIMS)),
                  condition.strip() if isinstance(condition, str) and condition.strip() else None,
                  tuple(a.check for a in answers))


def prompt(role: str, who: str, task: str) -> str:
    """A role's system prompt; it starts "Role: <role>." so a scripted model can tell roles apart."""
    return _BASE.format(role=role, who=who, task=task)


def _listed(claims: list[Claim]) -> str:
    return "\n".join(f"- ({c.role}) {c.text} {cited(c.cites)}" for c in claims) or "(none)"
