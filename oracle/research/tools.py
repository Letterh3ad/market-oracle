"""What the research loop can call. One string in, one short text out: a 4B model fills a
single field far more reliably than a nested argument object."""

from __future__ import annotations

from typing import Protocol


class ToolError(RuntimeError):
    """The tool could not answer; the loop shows the message to the model and carries on."""


class Tool(Protocol):
    name: str
    description: str
    """One line for the prompt: what it returns and what `input` should be."""

    async def run(self, input: str) -> str: ...
