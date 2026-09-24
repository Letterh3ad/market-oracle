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
        self.roles: dict[str, object] = {}
        """Role name -> its answer (an exception is raised; a callable is called with the messages), for the
        calls after the loop; others answer `blank`."""
        self.calls: list[tuple[list[dict], dict]] = []
        self.fell_back_to: set[str] = set()
        self.falls_back_to: str | None = None
        """A model every reply claims to have come from after a refusal."""

    async def reply(self, messages: list[dict], schema: dict) -> object:
        self.calls.append(([dict(m) for m in messages], schema))
        if self.falls_back_to:
            self.fell_back_to.add(self.falls_back_to)
        if "queries" in schema.get("properties", {}):
            return self.suggestions
        if role := _role(messages):
            answer = self.roles[role] if role in self.roles else blank(schema)
            if callable(answer):
                answer = answer(messages)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        if not self.replies:
            raise AssertionError("the script ran out of replies")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def step(action: str, input_: str = "", thought: str = "thinking") -> dict:
    return {"thought": thought, "action": action, "input": input_}


def blank(schema: dict) -> dict:
    """A well-formed answer that says nothing: what a role with nothing to add replies."""
    return {k: [] if v.get("type") == "array" else "" for k, v in schema.get("properties", {}).items()}


def _role(messages: list[dict]) -> str | None:
    first = messages[0]["content"] if messages else ""
    return first[len("Role: "):].split(".", 1)[0] if first.startswith("Role: ") else None
