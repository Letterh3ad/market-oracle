"""Ollama's /api/chat, adapted from the stalker's classifier. The schema goes in as
`format`, so Ollama constrains decoding to it. Not through the rate gate: localhost has no
budget to share."""

from __future__ import annotations

import json

import httpx

from oracle.research.llm import ModelError


class OllamaChat:
    name = "ollama"

    def __init__(self, model: str, base_url: str, timeout_s: float, context: int,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.model = model
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._context = context
        self._transport = transport

    async def reply(self, messages: list[dict], schema: dict) -> object:
        payload = {
            "model": self.model,
            "messages": messages,
            "format": schema,
            "stream": False,
            # Research reads evidence; sampling would only make reruns disagree.
            "options": {"temperature": 0, "num_ctx": self._context},
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                response = await http.post(f"{self._base}/api/chat", json=payload)
        except httpx.TransportError as exc:
            raise ModelError(f"Ollama is not reachable at {self._base} ({exc}): start it with `ollama serve`") from exc
        if response.status_code == 404:
            raise ModelError(f"Ollama has no model {self.model}: run `ollama pull {self.model}`")
        try:
            response.raise_for_status()
            content = response.json()["message"]["content"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ModelError(f"Ollama {self.model} gave no usable answer: {exc}") from exc
        try:
            return json.loads(content)
        except ValueError:
            return content
