"""The verdict role: after the six roles, one call on the listing (buy, hold or sell) from
everything the run found, not only the report, fitted into the model's context."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace

from oracle.research.budget import budget, reply_room
from oracle.research.checks import Answer
from oracle.research.citations import cited
from oracle.research.llm import ChatModel, ModelError
from oracle.research.roles import MAX_CITES, prompt
from oracle.research.trace import FINAL, VERDICTS, Drop, Evidence, Report, Step, Verdict

_IDS = re.compile(r"\[a(\d+)\]")

ROLE = "verdict"
WHO = "the portfolio manager"
TASK = ("Decide buy, hold or sell for the next days to few weeks, weighing the statistics, the researcher's "
        "notes, the team's outputs and the evidence. Give a confidence from 0 to 1, a one-sentence reason "
        'citing evidence, and one plain condition that would prove the verdict wrong. Reply with JSON: '
        '"verdict", "confidence", "reason", "cites" and "invalidated_if".')
SCHEMA = {"verdict": {"type": "string", "enum": list(VERDICTS)},
          "confidence": {"type": "number", "minimum": 0, "maximum": 1},
          "reason": {"type": "string"},
          "cites": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_CITES},
          "invalidated_if": {"type": "string"}}

# Kinds of material, in fill order: the last ones are the first left out.
_KINDS = {"notes": ("the researcher's notes", "the researcher's notes"),
          "role": ("role output", "role outputs"),
          "cited": ("cited item", "cited items"),
          "read": ("uncited item read", "uncited items read"),
          "observation": ("step observation", "step observations"),
          "uncited": ("uncited item", "uncited items")}
_HEADINGS = {"notes": "Researcher's notes:", "role": "The team's outputs:", "cited": "Evidence cited:",
             "read": "Other evidence read:",
             "observation": "What the research tools returned:", "uncited": "Other evidence shown:"}
# The order sections are shown in, which is not the fill order.
_SHOWN = ("notes", "role", "cited", "read", "uncited", "observation")
_REASONS = {Drop.WRONG_SHAPE: "its reason is not in the shape asked for", Drop.EMPTY_TEXT: "it gave no reason",
            Drop.NO_CITATION: "its reason cites no evidence",
            Drop.UNKNOWN_CITATION: "its reason cites nothing it was shown"}


@dataclass(frozen=True)
class Material:
    """Everything a run found, for the verdict role."""

    statistics: str
    """The stored-data line (prices, recent change, fundamentals, lean, analyst signals); never trimmed."""
    statistics_id: int
    notes: str
    report: Report
    evidence: tuple[Evidence, ...]
    """What the run was shown, other than the stored-data item."""
    steps: tuple[Step, ...]
    evidence_lines: dict[int, str]
    """How each evidence item reads to the model, by id."""
    read: frozenset[int] = frozenset()
    """Evidence whose line carries the facts read from it: kept before any headline alone."""


@dataclass(frozen=True)
class Fitted:
    text: str
    shown: set[int]
    unread: tuple[str, ...]


def fit(m: Material, chars: int) -> Fitted:
    """The material in fill order until the budget runs out; what was left out is counted by kind."""
    cites = m.report.cites | _ids(m.notes)
    items: list[tuple[str, str]] = [("notes", m.notes)] if m.notes.strip() else []
    items += [("role", line) for line in _role_lines(m.report)]
    read = [e for e in m.evidence if e.id in m.read]
    headlines = [e for e in m.evidence if e.id not in m.read]
    items += [("cited", m.evidence_lines[e.id]) for e in (*read, *headlines) if e.id in cites]
    items += [("read", m.evidence_lines[e.id]) for e in read if e.id not in cites]
    observations = [_observation(s) for s in m.steps if s.action != FINAL and s.observation]
    items += [("observation", o) for o in observations if _ids(o) & cites]
    items += [("observation", o) for o in observations if not _ids(o) & cites]
    items += [("uncited", m.evidence_lines[e.id]) for e in headlines if e.id not in cites]
    statistics = f"Statistics:\n{m.statistics}"
    room = chars - len(statistics)
    kept: dict[str, list[str]] = {k: [] for k in _KINDS}
    left_out: Counter[str] = Counter()
    for kind, text in items:
        # Strictly in order: nothing later takes room that something earlier did not fit in.
        cost = len(text) + 1 + (0 if kept[kind] else len(_HEADINGS[kind]) + 2)
        if left_out or cost > room:
            left_out[kind] += 1
        else:
            kept[kind].append(text)
            room -= cost
    sections = [statistics]
    sections += [f"{_HEADINGS[k]}\n" + "\n".join(kept[k]) for k in _SHOWN if kept[k]]
    text = "\n\n".join(sections)
    known = {m.statistics_id} | {e.id for e in m.evidence}
    unread = tuple(f"{n} {_KINDS[k][n != 1]}" if k != "notes" else _KINDS[k][0]
                   for k in _KINDS if (n := left_out[k]))
    return Fitted(text, _ids(text) & known, unread)


async def add_verdict(model: ChatModel, report: Report, material: Material, context_tokens: int) -> Report:
    """The report with the verdict, or with why there is none; it never guesses one. A backend
    that cannot answer costs only the verdict, never the report."""
    system = prompt(ROLE, WHO, TASK)
    fitted = fit(material, budget(context_tokens, system, reply_room(model)))
    try:
        reply = await model.reply([{"role": "system", "content": system}, {"role": "user", "content": fitted.text}],
                                  {"type": "object", "properties": SCHEMA, "required": list(SCHEMA)})
    except ModelError as exc:
        verdict, rejected = None, f"the model could not write a verdict: {exc}"
    else:
        verdict, rejected = check(reply, fitted.shown)
    return replace(report, verdict=verdict, verdict_rejected=rejected, unread=fitted.unread)


def check(reply: object, shown: set[int]) -> tuple[Verdict | None, str | None]:
    """A valid verdict, or None and why: the label is in the set, the confidence in range,
    and the reason cites something the verdict role was shown."""
    answer = Answer(ROLE, reply, {"verdict": str, "reason": str, "cites": list, "invalidated_if": str}, shown)
    if not answer.usable:
        return None, "not in the shape asked for"
    label = str(answer.get("verdict")).strip().lower()
    if label not in VERDICTS:
        return None, f"{label!r} is not buy, hold or sell"
    confidence = answer.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return None, f"confidence {confidence!r} is not a number from 0 to 1"
    reason = answer.case("reason")
    if reason is None:
        dropped = answer.check.dropped
        return None, _REASONS[next(iter(dropped))] if dropped else "it gave no reason"
    condition = str(answer.get("invalidated_if")).strip()
    if not condition:
        return None, "it gave no condition that would prove it wrong"
    return Verdict(label, float(confidence), reason.text, reason.cites, condition), None


def _role_lines(r: Report) -> list[str]:
    lines = [f"- {c.role}: {c.text} {cited(c.cites)}" for c in r.findings]
    lines += [f"- {side} case: {c.text} {cited(c.cites)}" for side, c in (("bull", r.bull), ("bear", r.bear)) if c]
    lines += [f"- risk: {c.text} {cited(c.cites)}" for c in r.risks]
    if r.invalidated_if:
        lines.append(f"- the bull case is wrong if: {r.invalidated_if}")
    return lines


def _observation(s: Step) -> str:
    return f"Step {s.n}, {s.action}({s.input}):\n{s.observation}"


def _ids(text: str) -> set[int]:
    return {int(i) for i in _IDS.findall(text)}
