"""Model profiles: building the model a task runs on, listing and testing profiles, and
editing them in oracle.toml. A new provider is a PROVIDERS entry in config plus a builder here."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, fields

from oracle.config import (
    ALLOW_CLAUDE_CODE, DEFAULT_PROFILE, PROVIDER_KEYS, PROVIDERS, URLLESS_PROVIDERS, ConfigError, LlmConfig,
    ModelProfile, check_profile, check_profile_name, model_profiles, profile_tables, with_settings, without_section,
)
from oracle.research.claude import ClaudeChat
from oracle.research.claude_code import ClaudeCodeChat
from oracle.research.llm import ChatModel, ModelError
from oracle.research.ollama import OllamaChat
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, JobAlreadyRunning, ModelUnavailable, UnknownModelProfile
from oracle.services.jobs_service import job_statuses
from oracle.services.settings_service import edit_config, reload_live_settings

__all__ = [
    "DEFAULT_PROFILE", "ModelTest", "RunModel", "delete_model_profile", "model_for", "model_profile_rows",
    "provider_defaults", "resolve_model", "save_model_profile", "try_model_profile",
]

Builder = Callable[[AppContext, str, ModelProfile], ChatModel]

BUILDERS: dict[str, Builder] = {
    "ollama": lambda ctx, name, p: OllamaChat(p.model, p.base_url, p.timeout_s, p.context, transport=ctx.transport),
    "claude_api": lambda ctx, name, p: ClaudeChat(p.model, p.timeout_s, api_key=_key(name, p), base_url=p.base_url,
                                                  **({"key_source": _key_source(name, p)} if p.key or p.key_env else {})),
    "claude_code": lambda ctx, name, p: ClaudeCodeChat(_allowed(ctx, name, p).model, p.timeout_s),
}


def _allowed(ctx: AppContext, name: str, p: ModelProfile) -> ModelProfile:
    # The loader refuses such a profile already; this covers a config built another way.
    if not ctx.config.allow_claude_code:
        raise ModelUnavailable(f"model profile {name}: claude_code is off; set {ALLOW_CLAUDE_CODE} = true under [models]")
    return p

MODEL_JOBS = ("research", "discovery", "discovery_chat")
"""Jobs that keep the model busy; a test beside one would only queue behind it."""

PROBE = [{"role": "system", "content": "You answer in JSON."}, {"role": "user", "content": 'Answer {"ok": true}.'}]
PROBE_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


@dataclass(frozen=True)
class ModelTest:
    name: str
    ok: bool
    seconds: float
    error: str | None = None


@dataclass(frozen=True)
class RunModel:
    """A run's model and how much it reads at once, resolved together once per run, so an
    edit to the profile mid-run cannot size one stage's prompts for another model."""

    model: ChatModel
    context: int


def resolve_model(ctx: AppContext, profile: str, model: ChatModel | None = None) -> RunModel:
    """The named profile's model (or `model`, which a test or caller supplies) and its context."""
    p = _profile(ctx, profile)
    return RunModel(model or BUILDERS[p.provider](ctx, profile, p), p.context)


def model_for(ctx: AppContext, profile: str) -> ChatModel:
    """The model the named profile describes."""
    return resolve_model(ctx, profile).model


def _profile(ctx: AppContext, name: str) -> ModelProfile:
    try:
        return model_profiles(ctx.config)[name]
    except KeyError:
        raise UnknownModelProfile(f"no model profile {name}") from None


def _key(name: str, p: ModelProfile) -> str | None:
    if p.key_env is None:
        return p.key
    value = os.environ.get(p.key_env)
    if not value:
        raise ModelUnavailable(f"model profile {name}: {p.key_env} is not set in the environment")
    return value


def _key_source(name: str, p: ModelProfile) -> str:
    return (f"check {p.key_env} (key_env of model profile {name})" if p.key_env
            else f"check the key saved in model profile {name}")


def model_profile_rows(ctx: AppContext) -> list[dict]:
    """Every profile, default first: its resolved values, the ones actually set (`set`,
    None where the provider's default applies), and its key only as a state."""
    tables = profile_tables(ctx.config)
    return [{
        "name": name, "provider": p.provider, "model": p.model, "base_url": p.base_url, "context": p.context,
        "timeout_s": p.timeout_s, "key_env": p.key_env, "key_state": _key_state(p),
        "key_fallback": PROVIDERS[p.provider].fallback_key, "from_llm": name not in ctx.config.models,
        "off": p.provider == "claude_code" and not ctx.config.allow_claude_code,
        "set": {f: getattr(tables[name], f) for f in ("base_url", "context", "timeout_s")},
    } for name, p in model_profiles(ctx.config).items()]


def provider_defaults(ctx: AppContext) -> dict[str, dict]:
    """Each provider offered, with what its unset keys mean and whether it takes a key or a
    URL. claude_code is offered only when `[models] allow_claude_code` is set."""
    return {name: {"base_url": spec.base_url, "context": spec.context, "timeout_s": LlmConfig.timeout_s,
                   "keyed": spec.fallback_key is not None, "url": name not in URLLESS_PROVIDERS}
            for name, spec in PROVIDERS.items() if name != "claude_code" or ctx.config.allow_claude_code}


def _key_state(p: ModelProfile) -> str:
    """set, unset (key_env names a missing variable), fallback (the provider's own lookup)
    or none (the provider takes no key)."""
    if PROVIDERS[p.provider].fallback_key is None:
        return "none"
    if p.key or p.key_env and os.environ.get(p.key_env):
        return "set"
    return "unset" if p.key_env else "fallback"


async def try_model_profile(ctx: AppContext, name: str) -> ModelTest:
    """One tiny schema-bound prompt: whether the profile answers, and how fast."""
    _profile(ctx, name)
    busy = next((s.job for s in job_statuses(ctx) if s.job in MODEL_JOBS and s.stale_reason is None), None)
    if busy:
        raise JobAlreadyRunning(f"{busy.replace('_', ' ')} is using the model: test once it finishes")
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
    literal `key` left out (None) is kept unless `key_env` replaces it; "" removes it. On a
    provider change, values equal to the old provider's (carried over by a form) and keys
    the new provider cannot take are dropped. Editing `default` writes `[models.default]`,
    which then overrides `[llm]`."""
    _checked(check_profile_name, name)
    known = {n.lower(): n for n in model_profiles(ctx.config)}
    if create and name.lower() in known:
        raise InvalidQuery(f"a profile named {known[name.lower()]} already exists")
    old = None if create else _profile(ctx, name)
    table = {k: v for k, v in values.items() if v is not None and v != ""}
    named = table.get("provider")
    provider = PROVIDERS.get(named) if isinstance(named, str) else None
    if old and provider and table["provider"] != old.provider:
        table = {k: v for k, v in table.items() if k not in PROVIDER_KEYS or v != getattr(old, k)}
    if named == "claude_code" and not ctx.config.allow_claude_code:
        raise InvalidQuery(f"claude_code is off: set {ALLOW_CLAUDE_CODE} = true under [models] in oracle.toml")
    if named in URLLESS_PROVIDERS:
        table.pop("base_url", None)
    if provider and provider.fallback_key is None:
        table = {k: v for k, v in table.items() if k not in ("key", "key_env")}
    elif values.get("key") is None and "key_env" not in table and old and old.key:
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
