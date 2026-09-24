"""Any OpenAI-style chat completions server (LM Studio, vLLM, llama.cpp, OpenRouter, OpenAI)
over plain HTTP. Replies are constrained by a strict json_schema response_format; a server
that refuses one is asked for a json_object with the schema in the prompt instead. A
parameter the model refuses (temperature, the output cap) is dropped or swapped. The model
remembers each of these, so later replies do not pay the refusal again. The key goes only
to base_url: redirects are not followed."""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

from oracle.research.budget import CHARS_PER_TOKEN, REPLY_TOKENS
from oracle.research.llm import ModelError, closed_schema

NO_KEY = "give the model profile a key or key_env in Settings > Models"
# Answers run to a few hundred tokens (Ollama's 1024 cap has room to spare); 8x that covers a
# model thinking in its content or hidden reasoning, when the prompt leaves that much room.
MAX_REPLY_TOKENS = 8192
REPLY_MARGIN = 256
"""Tokens kept free beside the estimated prompt for the chat template."""
CAP_NAMES = ("max_tokens", "max_completion_tokens", None)
"""The output cap's name, renamed as the model calls the one before unsupported; None sends none."""
OVERFLOW = re.compile(r"maximum context length|too large|exceeds? the (?:available )?context|\d+ in the completion")
THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)
FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
REFUSED = r"(?:unsupported|not supported|does not support|only the default)"


def chat_url(base_url: str) -> str:
    """base_url is the API root (`http://host:1234/v1`); a bare host gets `/v1`, and a full
    `/chat/completions` URL is taken as it is."""
    parts = urlsplit(base_url)
    path = parts.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path = f"{path or '/v1'}/chat/completions"
    return urlunsplit(parts._replace(path=path))


def shown_url(url: str) -> str:
    """The URL without credentials, query or fragment, safe to put in an error."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))


class OpenAICompatibleChat:
    name = "openai_compatible"

    def __init__(self, model: str, base_url: str, timeout_s: float, context: int, *, api_key: str | None = None,
                 key_source: str = NO_KEY, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """No `api_key`: no Authorization header, as local servers want. `key_source` tells
        the user where to fix a refused key. `context` sizes each reply's output cap."""
        self.model = model
        self._base = shown_url(base_url)
        self._url = chat_url(base_url)
        self._timeout = timeout_s
        self._context = context
        self._api_key = api_key
        self._key_source = key_source
        self._transport = transport
        self._schema_format = True
        self._temperature = True
        self._cap_name = 0
        """Index into CAP_NAMES."""
        self.fell_back_to: set[str] = set()
        """Always empty: the server never names a model that answered in this one's place."""

    async def reply(self, messages: list[dict], schema: dict) -> object:
        fell_back_now, capped = False, True
        while True:
            response = await self._post(self._payload(messages, schema, capped))
            if response.status_code not in (400, 422):
                break
            problem = _problem(response)
            cap_name = CAP_NAMES[self._cap_name]
            if self._temperature and _refuses(response, "temperature"):
                self._temperature = False
            elif OVERFLOW.search(problem.lower()):
                # The server's window is smaller than the profile says; the cap is the only give.
                if not capped or not cap_name:
                    raise ModelError(f"{self._where}: the prompt is longer than the server's context ({problem}); "
                                     "set the profile's context to the server's window in Settings > Models")
                capped = False
            elif cap_name and _refuses(response, cap_name):
                self._cap_name += 1
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
            if not isinstance(message, dict):
                raise TypeError(f"message is {type(message).__name__}")
            if message.get("refusal"):
                return None
            return _parsed(_answer(message))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelError(f"{self._where} gave no usable answer: {exc!r}"[:300]) from exc

    @property
    def _where(self) -> str:
        return f"{self.model} at {self._base}"

    def _payload(self, messages: list[dict], schema: dict, capped: bool) -> dict:
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
        cap_name = CAP_NAMES[self._cap_name]
        if capped and cap_name:
            payload[cap_name] = self._cap(payload["messages"], schema)
        return payload

    def _cap(self, messages: list[dict], schema: dict) -> int:
        """What the prompt leaves of the context, estimated as the budget cuts prompts, up to
        MAX_REPLY_TOKENS; never below the REPLY_TOKENS every prompt was cut to leave."""
        chars = sum(len(str(m.get("content") or "")) for m in messages) + len(json.dumps(schema))
        room = self._context - chars // CHARS_PER_TOKEN - REPLY_MARGIN
        return max(REPLY_TOKENS, min(MAX_REPLY_TOKENS, room))

    async def _post(self, payload: dict) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                # httpx times each read, so a server trickling tokens could outlast timeout_s many times over.
                return await asyncio.wait_for(http.post(self._url, json=payload, headers=headers), self._timeout)
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise ModelError(f"{self._where} took longer than {self._timeout:.0f}s to answer; "
                             "raise its timeout_s in Settings > Models if the model is still loading") from exc
        except httpx.TransportError as exc:
            raise ModelError(f"{self.model} is not reachable at {self._base} ({type(exc).__name__}): start the "
                             "server or check base_url") from exc

    def _check(self, response: httpx.Response) -> None:
        status = response.status_code
        if status in (401, 403):
            fix = f"refused the key: {self._key_source}" if self._api_key else f"wants a key: {NO_KEY}"
            raise ModelError(f"{self._where} {fix} ({_problem(response)})")
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


def _error(response: httpx.Response) -> dict:
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return {}
    return error if isinstance(error, dict) else {"message": error} if isinstance(error, str) else {}


def _problem(response: httpx.Response) -> str:
    message = _error(response).get("message")
    return str(message or response.text or response.reason_phrase)[:200]


def _refuses(response: httpx.Response, param: str) -> bool:
    """Whether the error is about `param`: named as the error's param, or beside a refusal in
    its message. Never the rest of the body, which may echo the request back."""
    error = _error(response)
    if error.get("param") == param:
        return True
    message = str(error.get("message") or "").lower()
    near = r"[^.]{0,60}"
    return re.search(rf"\b{param}\b{near}{REFUSED}|{REFUSED}{near}\b{param}\b", message) is not None


def _answer(message: dict) -> str:
    """The reply's text. Reasoning models may wrap their thinking in <think> tags (or only
    close one), or leave content empty and answer in `reasoning_content` or `reasoning`."""
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(_text(part.get("text"), missing=None) for part in content
                          if isinstance(part, dict) and part.get("type", "text") == "text")
    text = THINKING.sub("", _text(content))
    text = text.rsplit("</think>", 1)[-1].strip()
    return text or _text(message.get("reasoning_content")) or _text(message.get("reasoning"))


def _text(value: object, missing: str | None = "") -> str:
    """`value` as text; None gives `missing`, or is malformed where `missing` is None."""
    if value is None and missing is not None:
        return missing
    if not isinstance(value, str):
        raise TypeError(f"expected text, got {type(value).__name__}")
    return value


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
