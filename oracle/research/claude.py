"""Claude through the official Anthropic SDK: the same loop, queries and roles as on Ollama.
Replies come back as JSON constrained to the schema (structured outputs). Refusals fall back
server-side to Anthropic's recommended model for that category (`fallbacks: "default"`).
The key comes from the model profile (an environment variable it names, or a key kept in
oracle.toml), else from ANTHROPIC_API_KEY or an `ant auth login` profile."""

from __future__ import annotations

import copy
import json

import anthropic

from oracle.research.llm import ModelError

# Room for adaptive thinking plus a short JSON answer, under the SDK's non-streaming limits.
MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
NO_CREDENTIALS = "set ANTHROPIC_API_KEY or run `ant auth login`"


class ClaudeChat:
    name = "claude"

    def __init__(self, model: str, timeout_s: float, client: object | None = None, *,
                 api_key: str | None = None, base_url: str | None = None, key_source: str = NO_CREDENTIALS) -> None:
        """No `api_key`: the SDK's own lookup (ANTHROPIC_API_KEY, then `ant auth login`).
        `key_source` tells the user where to fix a refused key."""
        self.model = model
        self._timeout = timeout_s
        self._client = client
        self._api_key = api_key
        self._base_url = base_url
        self._key_source = key_source
        self.fell_back_to: set[str] = set()

    async def reply(self, messages: list[dict], schema: dict) -> object:
        request = {
            "model": self.model, "max_tokens": MAX_TOKENS, "messages": [m for m in messages if m["role"] != "system"],
            "system": "\n\n".join(m["content"] for m in messages if m["role"] == "system"),
            "output_config": {"format": {"type": "json_schema", "schema": _closed(schema)}},
            "betas": [FALLBACK_BETA], "fallbacks": "default",
        }
        try:
            if self._client is not None:
                response = await self._client.beta.messages.create(**request)
            else:
                async with anthropic.AsyncAnthropic(timeout=self._timeout, api_key=self._api_key,
                                                    base_url=self._base_url) as client:
                    response = await client.beta.messages.create(**request)
        except anthropic.APITimeoutError as exc:
            raise ModelError(f"Claude took longer than {self._timeout:.0f}s to answer; "
                             "raise its timeout_s in Settings > Models") from exc
        except anthropic.AuthenticationError as exc:
            raise ModelError(f"Claude refused the credentials: {self._key_source}") from exc
        except anthropic.RateLimitError as exc:
            raise ModelError("Claude is rate limiting this account: wait, or switch to an Ollama profile in "
                             "Settings > Models") from exc
        except anthropic.APIStatusError as exc:
            raise ModelError(f"Claude answered HTTP {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ModelError(f"Claude is not reachable ({exc}): check the internet connection") from exc
        except anthropic.AnthropicError as exc:
            # Raised before any request when no credential source resolves.
            raise ModelError(f"Claude could not start ({exc}): {self._key_source}") from exc
        # The served id alone cannot tell: an alias comes back as a longer dated id.
        iterations = getattr(getattr(response, "usage", None), "iterations", None) or []
        self.fell_back_to.update(i.model for i in iterations if i.type == "fallback_message")
        if response.stop_reason == "refusal":
            # Declined even after the fallback: no answer, which the loop treats as not understood.
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except ValueError:
            return text


def _closed(schema: dict) -> dict:
    """Structured outputs need every object closed and all its fields required, and take no
    array-length limits; the callers cap lengths themselves."""
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
