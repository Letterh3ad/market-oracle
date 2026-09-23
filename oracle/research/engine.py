"""The ReAct loop: reason, act, observe, sized for a 4B-8B local model. Short prompts, one
tool call per step, a hard step cap. No storage here: each step goes to `on_step` as it
happens, so a crash still leaves the trace up to that point."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from oracle.research.llm import ChatModel
from oracle.research.tools import Tool, ToolError
from oracle.research.trace import FINAL, RunStatus, Step

# Room for several observations inside a small model's context window.
MAX_OBSERVATION = 1500

SYSTEM = """You research a listed company for an investor holding for days to a few weeks.
Work in steps. Each step, reply with JSON: a short "thought", then an "action" and its "input".
Tools (action: what it does):
{tools}
- final: your answer. Put your findings in "input": what matters for the price and why, citing what the tools showed. Say what is unknown.
Call one tool per step. Never invent facts a tool did not show."""

FORCE_FINAL = "Step limit reached. Reply now with action \"final\" and your findings so far."


@dataclass(frozen=True)
class Outcome:
    status: RunStatus
    findings: str


async def react(model: ChatModel, tools: Sequence[Tool], task: str, max_steps: int,
                on_step: Callable[[Step], None]) -> Outcome:
    """Raises ModelError if the backend cannot answer; steps already taken were reported."""
    by_name = {t.name: t for t in tools}
    listing = "\n".join(f"- {t.name}: {t.description}" for t in tools)
    messages = [{"role": "system", "content": SYSTEM.format(tools=listing)}, {"role": "user", "content": task}]
    for n in range(1, max_steps + 1):
        reply = await model.reply(messages, step_schema([*by_name, FINAL]))
        messages.append({"role": "assistant", "content": json.dumps(reply)})
        parsed = _parse(reply)
        if parsed is None:
            observation = 'Not understood: reply with JSON holding "thought", "action" and "input".'
            on_step(Step(n, "", "", "", observation))
        else:
            thought, action, input_ = parsed
            if action == FINAL:
                on_step(Step(n, thought, FINAL, input_, None))
                return Outcome(RunStatus.DONE, input_)
            observation = await _observe(by_name, action, input_)
            on_step(Step(n, thought, action, input_, observation))
        messages.append({"role": "user", "content": f"Observation: {observation}"})
    messages.append({"role": "user", "content": FORCE_FINAL})
    parsed = _parse(await model.reply(messages, step_schema([FINAL])))
    thought, findings = (parsed[0], parsed[2]) if parsed else ("", "")
    on_step(Step(max_steps + 1, thought, FINAL, findings, None))
    return Outcome(RunStatus.STEP_CAP, findings)


async def _observe(tools: dict[str, Tool], action: str, input_: str) -> str:
    tool = tools.get(action)
    if tool is None:
        return f"No tool named {action!r}. Tools: {', '.join(tools)}, {FINAL}."
    try:
        text = await tool.run(input_)
    except ToolError as exc:
        return f"{action} failed: {exc}"
    return text if len(text) <= MAX_OBSERVATION else text[:MAX_OBSERVATION] + " [cut]"


def _parse(reply: object) -> tuple[str, str, str] | None:
    if not isinstance(reply, dict):
        return None
    values = tuple(reply.get(k) for k in ("thought", "action", "input"))
    return values if all(isinstance(v, str) for v in values) else None


def step_schema(actions: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "thought": {"type": "string"},
            "action": {"type": "string", "enum": actions},
            "input": {"type": "string"},
        },
        "required": ["thought", "action", "input"],
    }
