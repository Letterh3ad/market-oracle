"""The chat model the research loop talks to. Backends are swappable: Ollama by default,
others later; tests script one."""

from __future__ import annotations

import copy
from typing import Protocol


class ModelError(RuntimeError):
    """The backend could not answer at all (down, model missing, not JSON)."""


class ChatModel(Protocol):
    name: str
    """The backend, e.g. "ollama"."""
    model: str
    fell_back_to: set[str]
    """Models that answered in this one's place after a refusal; the caller clears it per run."""
    reply_tokens: int
    """Tokens every prompt leaves free for the reply (`budget.reply_room` reads it)."""

    async def reply(self, messages: list[dict], schema: dict) -> object:
        """The model's next message, parsed from JSON constrained to `schema`."""
        ...


def closed_schema(schema: dict) -> dict:
    """Strict structured outputs (Claude's and OpenAI's) need every object closed and all
    its fields required, and take no array-length limits; the callers cap lengths themselves."""
    out = copy.deepcopy(schema)

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        node.pop("maxItems", None)
        node.pop("minItems", None)
        if node.get("type") == "object":
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
        for child in (*node.get("properties", {}).values(), node.get("items")):
            walk(child)

    walk(out)
    return out
