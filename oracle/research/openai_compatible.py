"""Any OpenAI-style chat completions server (LM Studio, vLLM, llama.cpp, OpenRouter, OpenAI)
over plain HTTP. Replies are constrained by a strict json_schema response_format; a server
that refuses one is asked for a json_object with the schema in the prompt instead, and the
model remembers that, so later replies do not pay the refusal again. The key goes only to
base_url: redirects are not followed."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx

from oracle.research.llm import ModelError, closed_schema

NO_KEY = "give the model profile a key or key_env in Settings > Models"
THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)
FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def chat_url(base_url: str) -> str:
    """base_url is the API root (`http://host:1234/v1`); a bare host gets `/v1`, and a full
    `/chat/completions` URL is taken as it is."""
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/v1/chat/completions" if not urlsplit(base).path else f"{base}/chat/completions"


class OpenAICompatibleChat:
    name = "openai_compatible"

    def __init__(self, model: str, base_url: str, timeout_s: float, *, api_key: str | None = None,
                 key_source: str = NO_KEY, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """No `api_key`: no Authorization header, as local servers want. `key_source` tells
        the user where to fix a refused key."""
        self.model = model
        self._base = base_url
        self._url = chat_url(base_url)
        self._timeout = timeout_s
        self._api_key = api_key
        self._key_source = key_source
        self._transport = transport
        self._schema_format = True
        self._temperature = True
        self.fell_back_to: set[str] = set()
        """Always empty: the server never names a model that answered in this one's place."""

    async def reply(self, messages: list[dict], schema: dict) -> object:
        fell_back_now = False
        while True:
            response = await self._post(self._payload(messages, schema))
            if response.status_code not in (400, 422):
                break
            problem = _problem(response)
            # Reasoning models (OpenAI's o-series and gpt-5) take only their default temperature.
            if self._temperature and "temperature" in problem.lower():
                self._temperature = False
            elif self._schema_format:
                self._schema_format, fell_back_now = False, True
            elif fell_back_now:
                # Refused either way, so the format was likely not the problem: ask with the schema next time.
                self._schema_format = True
                raise ModelError(f"{self._where} refused both a json_schema and a json_object reply "
                                 f"(HTTP {response.status_code}: {problem})")
            else:
                raise ModelError(f"{self._where} refused the request (HTTP {response.status_code}: {problem})")
        self._check(response)
        try:
            message = response.json()["choices"][0]["message"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelError(f"{self._where} gave no usable answer: {exc!r}") from exc
        if message.get("refusal"):
            return None
        return _parsed(_answer(message))

    @property
    def _where(self) -> str:
        return f"{self.model} at {self._base}"

    def _payload(self, messages: list[dict], schema: dict) -> dict:
        payload: dict = {"model": self.model}
        if self._schema_format:
            payload["messages"] = messages
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "reply", "strict": True, "schema": closed_schema(schema)}}
        else:
            payload["messages"] = _with_schema(messages, schema)
            payload["response_format"] = {"type": "json_object"}
        if self._temperature:
            # Research reads evidence; sampling would only make reruns disagree.
            payload["temperature"] = 0
        return payload

    async def _post(self, payload: dict) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                return await http.post(self._url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ModelError(f"{self._where} took longer than {self._timeout:.0f}s to answer; "
                             "raise its timeout_s in Settings > Models if the model is still loading") from exc
        except httpx.TransportError as exc:
            raise ModelError(f"{self.model} is not reachable at {self._base} ({exc}): start the server "
                             "or check base_url") from exc

    def _check(self, response: httpx.Response) -> None:
        status = response.status_code
        if status in (401, 403):
            raise ModelError(f"{self._where} refused the key: {self._key_source}" if self._api_key
                             else f"{self._where} wants a key: {NO_KEY}")
        if status == 404:
            raise ModelError(f"{self._where} answered HTTP 404 ({_problem(response)}): check the model name, and "
                             "that base_url is the API root, like http://localhost:1234/v1")
        if status == 429:
            raise ModelError(f"{self._where} is rate limiting: wait, or pick another profile in Settings > Models")
        if status != 200:
            raise ModelError(f"{self._where} answered HTTP {status}: {_problem(response)}")


def _with_schema(messages: list[dict], schema: dict) -> list[dict]:
    """The schema in the first system message, as some chat templates take only one, first."""
    ask = f"Answer with one JSON object matching this JSON schema: {json.dumps(schema)}"
    if messages and messages[0]["role"] == "system":
        return [{**messages[0], "content": f"{messages[0]['content']}\n\n{ask}"}, *messages[1:]]
    return [{"role": "system", "content": ask}, *messages]


def _problem(response: httpx.Response) -> str:
    try:
        error = response.json().get("error")
        text = error.get("message") if isinstance(error, dict) else error
    except (ValueError, AttributeError):
        text = None
    return str(text or response.text or response.reason_phrase)[:200]


def _answer(message: dict) -> str:
    """The reply's text. Reasoning models may wrap their thinking in <think> tags, or leave
    content empty and answer in `reasoning_content` or `reasoning` (vLLM, OpenRouter)."""
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = THINKING.sub("", content or "").strip()
    return text or message.get("reasoning_content") or message.get("reasoning") or ""


def _parsed(text: str) -> object:
    """JSON from the text, a fenced block or the widest {...} in it; else the text, for the loop to reject."""
    text = text.strip()
    fenced = FENCE.match(text)
    candidates = [fenced.group(1) if fenced else text]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return text
