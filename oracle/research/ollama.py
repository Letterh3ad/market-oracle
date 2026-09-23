"""Ollama's /api/chat, adapted from the stalker's classifier. The schema goes in as
`format`, so Ollama constrains decoding to it. Not through the rate gate: localhost has no
budget to share."""

from __future__ import annotations

import json

import httpx

from oracle.research.llm import ModelError


# Room for any answer asked of it; a small model looping inside a JSON array stops here
# instead of running to the timeout (seen live: a role reply ran past 180s).
MAX_REPLY_TOKENS = 1024


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
            "options": {"temperature": 0, "num_ctx": self._context, "num_predict": MAX_REPLY_TOKENS},
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                response = await http.post(f"{self._base}/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ModelError(f"Ollama {self.model} took longer than {self._timeout:.0f}s to answer; "
                             "raise [llm] timeout_s if the model is still loading") from exc
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
