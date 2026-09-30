"""The stronger reader: a local Ollama chat model that reads many headlines in one call and
answers, for each one's subject company, whether it is about the company, which way it points
for the share price and its event type (Laya's fixed set). One short code a headline, keyed by
its number, so the constrained answer always has one code for every headline in order.

Its answers are choices, not probabilities: the chosen direction is stored with probability 1,
so 06's direction x probability reads +1, -1 or 0 an article. Relevance is 1 or 0."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from oracle.research.laya import DIRECTIONS, EVENT_TYPES, LayaInput, LayaScore

__all__ = ["BATCH", "CODES", "NOT_ABOUT", "OllamaReader", "ReaderError", "decode", "messages", "reader_tag", "schema"]

BATCH = 25
"""Headlines a call: the bake-off's size (DECISIONS, stronger reader picking rule)."""
NOT_ABOUT = "x"
_LETTER = {"up": "u", "down": "d", "none": "n"}
_EVENTS = list(EVENT_TYPES)
CODES = [NOT_ABOUT] + [f"{_LETTER[d]}{n}" for d in DIRECTIONS for n in range(len(_EVENTS))]
_DIRECTION = {v: k for k, v in _LETTER.items()}
TIMEOUT_S = 900.0
"""A call on a model partly on the CPU (qwen2.5:32b) reads a batch in minutes."""

SYSTEM = (
    "You label news headlines for a stock investor. Each numbered line gives a company and a headline. "
    "For each line, judge the headline for that company only, and answer with one code:\n"
    f'- "{NOT_ABOUT}" when the headline is not about that company and does not directly affect its business '
    "(another company with a similar name, a place, a person, a sports result, a general topic).\n"
    "- otherwise a letter and a digit. The letter is what the news means for the company's share price: "
    '"u" good news, "d" bad news, "n" neither or unclear (routine filings, product reviews, general updates). '
    "The digit is the kind of event:\n"
    + "\n".join(f"  {n} {name}: {words}" for n, (name, words) in enumerate(EVENT_TYPES.items()))
    + '\nAnswer with the codes in line order, on one line with no spaces, like {"codes":["n9","x","u0"]}.'
)


def reader_tag(model: str) -> str:
    """The model tag stored with every score, beside Laya's in `laya_scores`."""
    return f"ollama:{model}"


def messages(items: list[LayaInput]) -> list[dict]:
    lines = [f"{n}. Company: {i.company} | Headline: {' '.join(i.headline.split())}"
             + (f" | Snippet: {' '.join(i.snippet.split())}" if i.snippet else "")
             for n, i in enumerate(items, 1)]
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}]


def schema(n: int) -> dict:
    return {"type": "object", "properties": {"codes": {"type": "array", "items": {"type": "string", "enum": CODES},
                                                       "minItems": n, "maxItems": n}}, "required": ["codes"]}


def decode(code: object) -> LayaScore | None:
    """The score a code stands for; None for anything else."""
    if code == NOT_ABOUT:
        return LayaScore(0.0, "none", {"none": 1.0}, "other", 1.0)
    if not isinstance(code, str) or len(code) != 2 or code[0] not in _DIRECTION or not code[1].isdigit():
        return None
    n = int(code[1])
    if n >= len(_EVENTS):
        return None
    direction = _DIRECTION[code[0]]
    return LayaScore(1.0, direction, {direction: 1.0}, _EVENTS[n], 1.0)


class ReaderError(RuntimeError):
    """Ollama cannot be reached or has no such model."""


@dataclass
class ReaderStats:
    calls: int = 0
    items: int = 0
    unparsed: int = 0
    seconds: float = 0.0
    """Time spent waiting on Ollama only."""


class OllamaReader:
    """Scores through Ollama's /api/chat, temperature 0, one call a batch. Headlines left without a
    usable code are asked once more on their own call; still none, their score is None (the job
    leaves them unscored, so a later run asks again)."""

    device = "ollama"

    def __init__(self, model: str, base_url: str, *, context: int = 8192, timeout_s: float = TIMEOUT_S,
                 keep_alive: str | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self.model, self.tag = model, reader_tag(model)
        # Windows tries ::1 first for "localhost" and Ollama listens on IPv4: about 2 s a new connection.
        self._url = base_url.rstrip("/").replace("//localhost", "//127.0.0.1") + "/api/chat"
        self._context, self._keep_alive = context, keep_alive
        self._http = httpx.Client(timeout=timeout_s, transport=transport)
        self.stats = ReaderStats()

    def score(self, items: list[LayaInput]) -> list[LayaScore | None]:
        out = self._ask(items)
        missed = [n for n, s in enumerate(out) if s is None]
        if missed:
            again = self._ask([items[n] for n in missed])
            for n, s in zip(missed, again):
                out[n] = s
        self.stats.items += len(items)
        self.stats.unparsed += sum(s is None for s in out)
        return out

    def _ask(self, items: list[LayaInput]) -> list[LayaScore | None]:
        payload = {"model": self.model, "messages": messages(items), "format": schema(len(items)), "stream": False,
                   "think": False,
                   "options": {"temperature": 0, "num_ctx": self._context, "num_predict": 8 * len(items) + 32}}
        if self._keep_alive is not None:
            payload["keep_alive"] = self._keep_alive
        began = time.monotonic()
        try:
            response = self._http.post(self._url, json=payload)
        except httpx.TransportError as exc:
            raise ReaderError(f"Ollama is not reachable at {self._url} ({exc}): start it with `ollama serve`") from exc
        finally:
            self.stats.seconds += time.monotonic() - began
            self.stats.calls += 1
        if response.status_code == 404:
            raise ReaderError(f"Ollama has no model {self.model}: run `ollama pull {self.model}`")
        try:
            response.raise_for_status()
            answer = json.loads(response.json()["message"]["content"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return [None] * len(items)
        codes = answer.get("codes") if isinstance(answer, dict) else None
        if not isinstance(codes, list) or len(codes) != len(items):
            return [None] * len(items)
        return [decode(c) for c in codes]

    def close(self) -> None:
        self._http.close()
