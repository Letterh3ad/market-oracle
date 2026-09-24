"""Model profiles: building the model a task runs on, listing and testing profiles, and
editing them in oracle.toml. A new provider is a PROVIDER_DEFAULTS entry in config plus a
builder here."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, fields

from oracle.config import (
    DEFAULT_PROFILE, PROVIDER_DEFAULTS, ConfigError, ModelProfile, check_profile, check_profile_name, model_profiles, with_settings,
    without_section,
)
from oracle.research.claude import ClaudeChat
from oracle.research.llm import ChatModel, ModelError
from oracle.research.ollama import OllamaChat
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, ModelUnavailable, UnknownModelProfile
from oracle.services.settings_service import edit_config, reload_live_settings

__all__ = [
    "DEFAULT_PROFILE", "PROVIDERS", "ModelTest", "context_tokens", "delete_model_profile", "model_for", "model_profile_rows",
    "save_model_profile", "try_model_profile",
]

Builder = Callable[[AppContext, ModelProfile, str | None], ChatModel]

BUILDERS: dict[str, Builder] = {
    "ollama": lambda ctx, p, key: OllamaChat(p.model, p.base_url, p.timeout_s, p.context, transport=ctx.transport),
    "claude_api": lambda ctx, p, key: ClaudeChat(p.model, p.timeout_s, api_key=key, base_url=p.base_url),
}

PROVIDERS = tuple(PROVIDER_DEFAULTS)

PROBE = [{"role": "system", "content": "You answer in JSON."}, {"role": "user", "content": 'Answer {"ok": true}.'}]
PROBE_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


@dataclass(frozen=True)
class ModelTest:
    name: str
    ok: bool
    seconds: float
    error: str | None = None


def model_profile(ctx: AppContext, name: str) -> ModelProfile:
    try:
        return model_profiles(ctx.config)[name]
    except KeyError:
        raise UnknownModelProfile(f"no model profile {name}") from None


def model_for(ctx: AppContext, profile: str) -> ChatModel:
    """The model the named profile describes."""
    p = model_profile(ctx, profile)
    return BUILDERS[p.provider](ctx, p, _key(profile, p))


def context_tokens(ctx: AppContext, profile: str) -> int:
    """How much the profile's model reads at once."""
    return model_profile(ctx, profile).context


def _key(name: str, p: ModelProfile) -> str | None:
    if p.key_env is None:
        return p.key
    value = os.environ.get(p.key_env)
    if not value:
        raise ModelUnavailable(f"model profile {name}: {p.key_env} is not set in the environment")
    return value


def model_profile_rows(ctx: AppContext) -> list[dict]:
    """Every profile, default first; a key only as set or not."""
    return [{
        "name": name, "provider": p.provider, "model": p.model, "base_url": p.base_url, "context": p.context,
        "timeout_s": p.timeout_s, "key_env": p.key_env,
        "key_set": bool(p.key or p.key_env and os.environ.get(p.key_env)),
        "from_llm": name not in ctx.config.models,
    } for name, p in model_profiles(ctx.config).items()]


async def try_model_profile(ctx: AppContext, name: str) -> ModelTest:
    """One tiny schema-bound prompt: whether the profile answers, and how fast."""
    start = time.perf_counter()
    try:
        reply = await model_for(ctx, name).reply(PROBE, PROBE_SCHEMA)
    except (ModelError, ModelUnavailable) as exc:
        return ModelTest(name, False, time.perf_counter() - start, str(exc))
    seconds = time.perf_counter() - start
    if not (isinstance(reply, dict) and isinstance(reply.get("ok"), bool)):
        return ModelTest(name, False, seconds, f"answered outside the schema: {str(reply)[:200]!r}")
    return ModelTest(name, True, seconds)


PROFILE_KEYS = tuple(f.name for f in fields(ModelProfile))


def save_model_profile(ctx: AppContext, name: str, values: dict[str, object], create: bool) -> None:
    """Add (`create`) or replace a profile; keys left out take the provider's defaults. A
    literal `key` left out (None) is kept unless `key_env` replaces it; "" removes it.
    Editing `default` writes `[models.default]`, which then overrides `[llm]`."""
    _checked(check_profile_name, name)
    known = {n.lower(): n for n in model_profiles(ctx.config)}
    if create and name.lower() in known:
        raise InvalidQuery(f"a profile named {known[name.lower()]} already exists")
    if not create:
        model_profile(ctx, name)
    table = {k: v for k, v in values.items() if v is not None and v != ""}
    old = ctx.config.models.get(name)
    if values.get("key") is None and "key_env" not in table and old and old.key:
        table["key"] = old.key
    _checked(check_profile, name, table)
    section = f"models.{name}"
    edit_config(ctx, lambda text: with_settings(text, section, {k: table.get(k) for k in PROFILE_KEYS}),
                f"edit [{section}]")
    reload_live_settings(ctx)


def delete_model_profile(ctx: AppContext, name: str) -> None:
    if name == DEFAULT_PROFILE:
        raise InvalidQuery("the default profile cannot be deleted")
    if name not in ctx.config.models:
        raise UnknownModelProfile(f"no model profile {name}")
    edit_config(ctx, lambda text: without_section(text, f"models.{name}"), f"remove [models.{name}]")
    reload_live_settings(ctx)


def _checked(check, *args):
    try:
        return check(*args)
    except ConfigError as exc:
        raise InvalidQuery(str(exc)) from exc
