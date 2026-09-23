"""A chat model that plays back scripted replies, recording what it was sent."""

from __future__ import annotations

from oracle.research.llm import ModelError


class ScriptedModel:
    name = "scripted"
    model = "script"

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.suggestions: object = {"queries": []}
        """The answer to the query-suggestion call, which comes before the loop's steps."""
        self.calls: list[tuple[list[dict], dict]] = []

    async def reply(self, messages: list[dict], schema: dict) -> object:
        self.calls.append(([dict(m) for m in messages], schema))
        if "queries" in schema.get("properties", {}):
            return self.suggestions
        if not self.replies:
            raise AssertionError("the script ran out of replies")
        reply = self.replies.pop(0)
        if isinstance(reply, ModelError):
            raise reply
        return reply


def step(action: str, input_: str = "", thought: str = "thinking") -> dict:
    return {"thought": thought, "action": action, "input": input_}
