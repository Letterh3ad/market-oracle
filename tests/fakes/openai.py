"""A fake OpenAI-style /v1/chat/completions server, answering scripted message contents in order."""

from __future__ import annotations

import json

import httpx


class FakeOpenAI:
    hosts = {"llm.test"}

    def __init__(self, *contents, key: str | None = None, schema_format: bool = True,
                 temperature: bool = True, cap: str | None = "max_tokens", window: int | None = None,
                 wording: int = 0) -> None:
        self.contents = list(contents)
        """Strings sent as-is, dicts as a whole `message`-override, anything else as JSON text."""
        self.key = key
        """The key the server wants; None takes any request."""
        self.schema_format = schema_format
        """False: refuses a json_schema response_format with 400, as some servers do."""
        self.temperature = temperature
        """False: refuses any temperature but the default, as reasoning models do."""
        self.cap = cap
        """The output cap it takes; the other is refused as OpenAI refuses it. None: refuses both."""
        self.window = window
        """Tokens of context (4 characters each here); a longer prompt plus output cap is refused as vLLM does."""
        self.wording = wording
        """Which of vLLM's two overflow messages it answers with."""
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []
        self.down = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        self.requests.append(request)
        body = json.loads(request.content)
        self.bodies.append(body)
        if request.url.path != "/v1/chat/completions":
            return httpx.Response(404, json={"error": {"message": "not found"}})
        if self.key and request.headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}})
        if not self.temperature and "temperature" in body:
            return _refused("Unsupported value: 'temperature' does not support 0 with this model.")
        for cap in ("max_tokens", "max_completion_tokens"):
            if cap in body and cap != self.cap:
                return _refused(f"Unsupported parameter: '{cap}' is not supported with this model.")
        if self.window:
            prompt = sum(len(m["content"]) for m in body["messages"]) // 4
            asked = body.get("max_tokens") or body.get("max_completion_tokens") or 0
            if prompt + asked > self.window:
                return _refused(OVERFLOW[self.wording].format(window=self.window, prompt=prompt, asked=asked,
                                                              total=prompt + asked))
        if not self.schema_format and body.get("response_format", {}).get("type") == "json_schema":
            return _refused("response_format json_schema is not supported by this server")
        content = self.contents.pop(0)
        if isinstance(content, dict) and "message" in content:
            message = {"role": "assistant", **content["message"]}
        else:
            message = {"role": "assistant", "content": content if isinstance(content, str) else json.dumps(content)}
        return httpx.Response(200, json={"id": "c1", "model": body["model"],
                                         "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})


OVERFLOW = (
    "This model's maximum context length is {window} tokens. However, you requested {total} tokens ({prompt} in the "
    "messages, {asked} in the completion). Please reduce the length of the messages or completion.",
    "'max_tokens' or 'max_completion_tokens' is too large: {asked}. This model's maximum context length is {window} "
    "tokens and your request has {prompt} input tokens ({asked} > {window} - {prompt}).",
)


def _refused(message: str) -> httpx.Response:
    return httpx.Response(400, json={"error": {"message": message, "type": "invalid_request_error"}})
