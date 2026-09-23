"""A fake Ollama /api/chat, answering scripted message contents in order."""

from __future__ import annotations

import json

import httpx


class FakeOllama:
    hosts = {"localhost"}

    def __init__(self, *contents, models=("qwen3:4b-instruct-2507-q8_0",)) -> None:
        self.contents = list(contents)
        """Strings sent as-is; anything else is sent as JSON text, as Ollama would."""
        self.models = set(models)
        self.bodies: list[dict] = []
        self.down = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content)
        self.bodies.append(body)
        if body["model"] not in self.models:
            return httpx.Response(404, json={"error": f"model '{body['model']}' not found"})
        content = self.contents.pop(0)
        text = content if isinstance(content, str) else json.dumps(content)
        return httpx.Response(200, json={"model": body["model"], "message": {"role": "assistant", "content": text},
                                         "done": True})
