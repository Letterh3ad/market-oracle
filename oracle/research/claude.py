"""Claude through the official Anthropic SDK: the same loop, queries and roles as on Ollama.
Replies come back as JSON constrained to the schema (structured outputs). Refusals fall back
server-side to Anthropic's recommended model for that category (`fallbacks: "default"`).
Credentials come from the environment (ANTHROPIC_API_KEY) or an `ant auth login` profile;
none is ever stored in oracle.toml."""

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

    def __init__(self, model: str, timeout_s: float, client: object | None = None) -> None:
        self.model = model
        self._timeout = timeout_s
        self._client = client

    async def reply(self, messages: list[dict], schema: dict) -> object:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [m for m in messages if m["role"] != "system"]
        try:
            client = self._client or anthropic.AsyncAnthropic(timeout=self._timeout)
            response = await client.beta.messages.create(
                model=self.model, max_tokens=MAX_TOKENS, system=system, messages=turns,
                output_config={"format": {"type": "json_schema", "schema": _closed(schema)}},
                betas=[FALLBACK_BETA], fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise ModelError(f"Claude refused the credentials: {NO_CREDENTIALS}") from exc
        except anthropic.RateLimitError as exc:
            raise ModelError("Claude is rate limiting this account: wait, or switch [llm] backend to ollama") from exc
        except anthropic.APIStatusError as exc:
            raise ModelError(f"Claude answered HTTP {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ModelError(f"Claude is not reachable ({exc}): check the internet connection") from exc
        except anthropic.AnthropicError as exc:
            # Raised before any request when no credential source resolves.
            raise ModelError(f"Claude could not start ({exc}): {NO_CREDENTIALS}") from exc
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
