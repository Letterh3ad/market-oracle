"""The chat model the research loop talks to. Backends are swappable: Ollama by default,
others later; tests script one."""

from __future__ import annotations

from typing import Protocol


class ModelError(RuntimeError):
    """The backend could not answer at all (down, model missing, not JSON)."""


class ChatModel(Protocol):
    name: str
    """The backend, e.g. "ollama"."""
    model: str

    async def reply(self, messages: list[dict], schema: dict) -> object:
        """The model's next message, parsed from JSON constrained to `schema`."""
        ...
