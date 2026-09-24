"""How much text fits in the model's context beside a system prompt and its reply."""

from __future__ import annotations

# English prose runs about 4 characters a token, but numbers and URLs nearer 3: filling at 3
# keeps a quarter in hand so the model never truncates the material itself.
CHARS_PER_TOKEN = 3
# The reply is short, but the schema's grammar and the chat template take room too.
REPLY_TOKENS = 1024


def budget(context_tokens: int, system: str, reply_tokens: int = REPLY_TOKENS) -> int:
    """Characters of material that fit beside the system prompt and the reply."""
    return max(0, (context_tokens - reply_tokens) * CHARS_PER_TOKEN - len(system))


def reply_room(model: object) -> int:
    """Tokens the model's reply needs kept free; a model that names none (scripted ones) takes the default."""
    return getattr(model, "reply_tokens", REPLY_TOKENS)


def cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
