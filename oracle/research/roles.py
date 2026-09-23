"""The research team's roles, after TradingAgents' structure (Apache-2.0; structure
reimplemented, no text copied): three analysts read the evidence, a bull and a bear argue
from their findings, and a risk manager says what could go wrong. Prompts are short and
each role is one call, for a 4B model."""

from __future__ import annotations

import re
from datetime import date

from oracle.research.llm import ChatModel
from oracle.research.trace import Claim, Report

MAX_CLAIMS = 3

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
_CITES = {"type": "array", "items": {"type": "string"}, "maxItems": 5}
_CLAIM_LIST = {"type": "array", "maxItems": MAX_CLAIMS,
               "items": {"type": "object", "properties": {"text": {"type": "string"}, "cites": _CITES},
                         "required": ["text", "cites"]}}
_ID = re.compile(r"a?(\d+)")


async def write_report(model: ChatModel, evidence: str, shown: set[int], day: date) -> Report:
    """Raises ModelError only if the backend cannot answer; a garbled role adds nothing."""

    async def ask(role: str, who: str, task: str, material: str, schema: dict) -> object:
        system = _BASE.format(role=role, who=who, task=task)
        return await model.reply([{"role": "system", "content": system}, {"role": "user", "content": material}],
                                 {"type": "object", "properties": schema, "required": list(schema)})

    findings: list[Claim] = []
    for role, (who, task) in ANALYSTS.items():
        reply = await ask(role, who, task, evidence, {"findings": _CLAIM_LIST})
        findings += _claims(_field(reply, "findings"), role, shown)[:MAX_CLAIMS]
    material = f"{evidence}\n\nThe analysts' findings:\n{_listed(findings)}"
    sides = {}
    for role, (who, task) in SIDES.items():
        reply = await ask(role, who, task, material, {"case": {"type": "string"}, "cites": _CITES})
        sides[role] = _claim(reply, "case", role, shown)
    cases = "\n".join(f"{c.role}: {c.text}" for c in sides.values() if c)
    reply = await ask("risk", *RISK, f"{material}\n\nThe cases:\n{cases}",
                      {"risks": _CLAIM_LIST, "invalidated_if": {"type": "string"}})
    condition = _field(reply, "invalidated_if")
    return Report(day, tuple(findings), sides["bull"], sides["bear"],
                  tuple(_claims(_field(reply, "risks"), "risk", shown)[:MAX_CLAIMS]),
                  condition.strip() if isinstance(condition, str) and condition.strip() else None)


def _field(reply: object, name: str) -> object:
    return reply.get(name) if isinstance(reply, dict) else None


def _claims(items: object, role: str, shown: set[int]) -> list[Claim]:
    if not isinstance(items, list):
        return []
    return [c for item in items if (c := _claim(item, "text", role, shown))]


def _claim(item: object, text_field: str, role: str, shown: set[int]) -> Claim | None:
    text, cites = _field(item, text_field), _field(item, "cites")
    if not isinstance(text, str) or not text.strip() or not isinstance(cites, list):
        return None
    ids: list[int] = []
    for cite in cites:
        m = _ID.fullmatch(str(cite).strip("[]() ").lower())
        if m and int(m.group(1)) in shown and int(m.group(1)) not in ids:
            ids.append(int(m.group(1)))
    return Claim(text.strip(), tuple(ids), role) if ids else None


def _listed(claims: list[Claim]) -> str:
    return "\n".join(f"- ({c.role}) {c.text} {' '.join(f'[a{i}]' for i in c.cites)}" for c in claims) or "(none)"
