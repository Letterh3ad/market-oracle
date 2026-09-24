from dataclasses import replace

import httpx
import pytest

from oracle.config import CLAUDE_CONTEXT, PROVIDERS, load_config
from oracle.research.claude import ClaudeChat
from oracle.research.claude_code import BIN_ENV, ClaudeCodeChat
from oracle.research.ollama import OllamaChat
from oracle.services import models_service
from oracle.services.context import load_settings, open_context
from oracle.services.errors import InvalidQuery, JobAlreadyRunning, ModelUnavailable, UnknownModelProfile
from oracle.services.models_service import (
    delete_model_profile, model_for, model_profile_rows, provider_defaults, resolve_model, save_model_profile,
    try_model_profile,
)
from tests.fakes.claude_cli import shim
from tests.fakes.ollama import FakeOllama


def _ctx(tmp_path, text="", transport=None):
    path = tmp_path / "oracle.toml"
    path.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"\n{text}', encoding="utf-8")
    config, source = load_settings(path)
    return open_context(config, source, transport=transport)


def test_every_provider_has_a_builder():
    assert set(models_service.BUILDERS) == set(PROVIDERS)


def test_a_toml_with_only_llm_yields_the_same_model_as_before(tmp_path):
    with _ctx(tmp_path, '[llm]\nmodel = "qwen3:8b"\nbase_url = "http://gpu:11434"\ntimeout_s = 60\ncontext = 8192\n') as ctx:
        llm = ctx.config.llm
        before = OllamaChat(llm.model, llm.base_url, llm.timeout_s, llm.context, transport=ctx.transport)
        built = model_for(ctx, "default")
        assert type(built) is OllamaChat and vars(built) == vars(before)
        assert resolve_model(ctx, "default").context == 8192
    with _ctx(tmp_path, '[llm]\nbackend = "claude"\nclaude_model = "claude-sonnet-5"\n') as ctx:
        built = model_for(ctx, "default")
        assert type(built) is ClaudeChat and vars(built) == vars(ClaudeChat("claude-sonnet-5", 180.0))
        assert resolve_model(ctx, "default").context == CLAUDE_CONTEXT


def test_a_profile_builds_its_provider_with_its_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_CLAUDE", "sk-env")
    with _ctx(tmp_path, '[models.deep]\nprovider = "claude_api"\nmodel = "claude-opus-5"\nkey_env = "MY_CLAUDE"\n'
                        'context = 100000\n[models.lit]\nprovider = "claude_api"\nmodel = "m"\nkey = "sk-lit"\n'
                        '[models.gone]\nprovider = "claude_api"\nmodel = "m"\nkey_env = "NOT_SET_ANYWHERE"\n') as ctx:
        assert vars(model_for(ctx, "deep")) == vars(ClaudeChat(
            "claude-opus-5", 180.0, api_key="sk-env", key_source="check MY_CLAUDE (key_env of model profile deep)"))
        assert resolve_model(ctx, "deep").context == 100000
        assert model_for(ctx, "lit")._api_key == "sk-lit"
        with pytest.raises(ModelUnavailable, match="NOT_SET_ANYWHERE is not set"):
            model_for(ctx, "gone")
        with pytest.raises(UnknownModelProfile, match="no model profile nope"):
            model_for(ctx, "nope")


def test_rows_show_each_profile_and_whether_its_key_is_set_never_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_CLAUDE", "sk-env")
    with _ctx(tmp_path, '[models.deep]\nprovider = "claude_api"\nmodel = "claude-opus-5"\nkey_env = "MY_CLAUDE"\n'
                        '[models.lit]\nprovider = "claude_api"\nmodel = "m"\nkey = "sk-lit"\n'
                        '[models.gone]\nprovider = "claude_api"\nmodel = "m"\nkey_env = "NOT_SET_ANYWHERE"\n') as ctx:
        rows = model_profile_rows(ctx)
    assert "sk-" not in repr(rows)
    assert [r["name"] for r in rows] == ["default", "deep", "lit", "gone"]
    default, deep, lit, gone = rows
    assert (default["provider"], default["model"], default["context"], default["from_llm"]) == (
        "ollama", "qwen3:4b-instruct-2507-q8_0", 16384, True)
    assert (default["key_state"], deep["key_state"], lit["key_state"], gone["key_state"]) == (
        "none", "set", "set", "unset")
    assert (default["key_fallback"], deep["key_fallback"]) == (None, "env / ant login")
    assert default["set"] == {"base_url": None, "context": None, "timeout_s": None}
    assert (deep["key_env"], lit["key_env"], deep["from_llm"]) == ("MY_CLAUDE", None, False)


async def test_a_test_reports_ok_and_the_reply_time(tmp_path):
    ollama = FakeOllama({"ok": True})
    with _ctx(tmp_path, transport=httpx.MockTransport(ollama.handle)) as ctx:
        result = await try_model_profile(ctx, "default")
    assert result.ok and result.error is None and result.seconds >= 0
    assert ollama.bodies[0]["format"]["required"] == ["ok"]


@pytest.mark.parametrize("answers, down, says", [
    ((), True, "not reachable"),
    (("not json",), False, "answered outside the schema"),
])
async def test_a_test_reports_the_error(tmp_path, answers, down, says):
    ollama = FakeOllama(*answers)
    ollama.down = down
    with _ctx(tmp_path, transport=httpx.MockTransport(ollama.handle)) as ctx:
        result = await try_model_profile(ctx, "default")
    assert not result.ok and says in result.error


async def test_a_test_of_a_profile_whose_key_is_missing_reports_it(tmp_path):
    with _ctx(tmp_path, '[models.gone]\nprovider = "claude_api"\nmodel = "m"\nkey_env = "NOT_SET_ANYWHERE"\n') as ctx:
        result = await try_model_profile(ctx, "gone")
    assert not result.ok and "NOT_SET_ANYWHERE" in result.error


def test_a_profile_is_added_edited_and_deleted_in_the_file_and_applies_at_once(tmp_path):
    with _ctx(tmp_path, "# mine\n[llm]\nmax_steps = 9\n") as ctx:
        save_model_profile(ctx, "big", {"provider": "ollama", "model": "qwen3:8b", "context": 8192}, create=True)
        assert model_for(ctx, "big").model == "qwen3:8b" and resolve_model(ctx, "big").context == 8192
        save_model_profile(ctx, "big", {"provider": "ollama", "model": "qwen3:14b"}, create=False)
        assert resolve_model(ctx, "big").context == 16384, "an edit replaces the profile; unset keys take defaults"
        on_disk = load_config(ctx.config_source)[0]
        assert on_disk.models["big"].model == "qwen3:14b" and on_disk.llm.max_steps == 9
        delete_model_profile(ctx, "big")
        assert "big" not in ctx.config.models and "big" not in load_config(ctx.config_source)[0].models
        assert "# mine" in ctx.config_source.read_text(encoding="utf-8")


def test_a_literal_key_is_kept_when_an_edit_leaves_it_out_and_cleared_or_replaced_on_request(tmp_path):
    claude = {"provider": "claude_api", "model": "claude-opus-5"}
    with _ctx(tmp_path) as ctx:
        save_model_profile(ctx, "deep", {**claude, "key": "sk-1"}, create=True)
        save_model_profile(ctx, "deep", {**claude, "timeout_s": 60}, create=False)
        assert ctx.config.models["deep"].key == "sk-1"
        save_model_profile(ctx, "deep", {**claude, "key_env": "MY_CLAUDE"}, create=False)
        assert (ctx.config.models["deep"].key, ctx.config.models["deep"].key_env) == (None, "MY_CLAUDE")
        save_model_profile(ctx, "deep", {**claude, "key": "sk-2"}, create=False)
        assert (ctx.config.models["deep"].key, ctx.config.models["deep"].key_env) == ("sk-2", None)
        save_model_profile(ctx, "deep", {**claude, "key": ""}, create=False)
        assert ctx.config.models["deep"].key is None


def test_editing_the_default_writes_a_default_profile(tmp_path):
    with _ctx(tmp_path) as ctx:
        save_model_profile(ctx, "default", {"provider": "ollama", "model": "gemma3:12b"}, create=False)
        assert model_for(ctx, "default").model == "gemma3:12b"
        assert model_profile_rows(ctx)[0]["from_llm"] is False


def test_bad_changes_are_refused_and_nothing_is_written(tmp_path):
    with _ctx(tmp_path) as ctx:
        save_model_profile(ctx, "big", {"provider": "ollama", "model": "qwen3:8b"}, create=True)
        before = ctx.config_source.read_bytes()
        cases = [
            (lambda: save_model_profile(ctx, "BIG", {"provider": "ollama", "model": "x"}, create=True),
             "a profile named big already exists"),
            (lambda: save_model_profile(ctx, "default", {"provider": "ollama", "model": "x"}, create=True),
             "a profile named default already exists"),
            (lambda: save_model_profile(ctx, "nope", {"provider": "ollama", "model": "x"}, create=False),
             "no model profile nope"),
            (lambda: save_model_profile(ctx, "big", {"provider": "gpt", "model": "x"}, create=False),
             "models.big.provider must be one of"),
            (lambda: save_model_profile(ctx, "a b", {"provider": "ollama", "model": "x"}, create=True),
             "not a profile name"),
            (lambda: save_model_profile(ctx, "big", {"provider": "ollama", "model": "x", "shade": 1}, create=False),
             "unknown key"),
            (lambda: delete_model_profile(ctx, "default"), "the default profile cannot be deleted"),
            (lambda: delete_model_profile(ctx, "nope"), "no model profile nope"),
        ]
        for call, says in cases:
            with pytest.raises((InvalidQuery, UnknownModelProfile), match=says):
                call()
        assert ctx.config_source.read_bytes() == before


def test_a_claude_profile_with_no_key_uses_the_providers_own_lookup(tmp_path):
    with _ctx(tmp_path, '[models.deep]\nprovider = "claude_api"\nmodel = "claude-opus-5"\ncontext = 100000\n') as ctx:
        [_, deep] = model_profile_rows(ctx)
    assert deep["key_state"] == "fallback" and deep["key_fallback"] == "env / ant login"
    assert deep["set"] == {"base_url": None, "context": 100000, "timeout_s": None}


def test_switching_the_defaults_provider_carries_none_of_the_old_providers_values_over(tmp_path):
    with _ctx(tmp_path) as ctx:
        default = model_profile_rows(ctx)[0]
        # What a form pre-filled from the resolved values would send back.
        carried = {k: default[k] for k in ("base_url", "context", "timeout_s")}
        save_model_profile(ctx, "default", {"provider": "claude_api", "model": "claude-opus-5", **carried},
                           create=False)
        claude = resolve_model(ctx, "default")
        assert (claude.model._base_url, claude.context) == (None, CLAUDE_CONTEXT)
        save_model_profile(ctx, "default", {"provider": "claude_api", "model": "claude-opus-5", "key": "sk-1"},
                           create=False)
        save_model_profile(ctx, "default", {"provider": "ollama", "model": "qwen3:8b", "context": CLAUDE_CONTEXT},
                           create=False)
        ollama = resolve_model(ctx, "default")
        assert (ollama.model._base, ollama.context) == ("http://localhost:11434", 16384)
        assert ctx.config.models["default"].key is None, "a key is dropped for a provider that takes none"


def test_a_value_set_on_purpose_survives_a_provider_switch(tmp_path):
    with _ctx(tmp_path) as ctx:
        save_model_profile(ctx, "default", {"provider": "claude_api", "model": "m", "context": 100000}, create=False)
        assert resolve_model(ctx, "default").context == 100000


def test_a_key_given_to_a_keyless_provider_is_dropped(tmp_path):
    with _ctx(tmp_path) as ctx:
        save_model_profile(ctx, "big", {"provider": "ollama", "model": "x", "key_env": "K"}, create=True)
        assert ctx.config.models["big"].key_env is None


async def test_a_test_waits_for_no_job_holding_the_model(tmp_path):
    from oracle.jobs import JobLock

    with _ctx(tmp_path) as ctx:
        handle = JobLock(ctx.conn, ctx.clock).acquire("research", 600)
        with pytest.raises(JobAlreadyRunning, match="research is using the model"):
            await try_model_profile(ctx, "default")
        JobLock(ctx.conn, ctx.clock).release(handle)


def test_the_jobs_that_hold_the_model_are_the_model_jobs():
    from oracle.services import discovery_chat_service, discovery_service, research_service

    assert set(models_service.MODEL_JOBS) == {research_service.JOB, discovery_service.JOB, discovery_chat_service.JOB}


def test_a_profile_the_file_writes_inline_is_an_edit_by_hand_error(tmp_path):
    with _ctx(tmp_path, 'models = { big = { provider = "ollama", model = "x" } }\n') as ctx:
        before = ctx.config_source.read_bytes()
        with pytest.raises(InvalidQuery, match="by hand"):
            delete_model_profile(ctx, "big")
        with pytest.raises(InvalidQuery, match="by hand"):
            save_model_profile(ctx, "small", {"provider": "ollama", "model": "y"}, create=True)
        assert ctx.config_source.read_bytes() == before


ALLOWED = "[models]\nallow_claude_code = true\n"
SUB = '[models.sub]\nprovider = "claude_code"\nmodel = "sonnet"\ntimeout_s = 30\n'


def test_claude_code_is_offered_only_when_the_flag_allows_it(tmp_path):
    with _ctx(tmp_path) as ctx:
        assert "claude_code" not in provider_defaults(ctx)
    with _ctx(tmp_path, ALLOWED) as ctx:
        assert provider_defaults(ctx)["claude_code"] == {
            "base_url": None, "context": CLAUDE_CONTEXT, "timeout_s": 180.0, "keyed": False, "url": False}
        assert provider_defaults(ctx)["ollama"]["url"] is True


def test_a_claude_code_profile_builds_the_cli_backend(tmp_path):
    with _ctx(tmp_path, ALLOWED + SUB) as ctx:
        built = model_for(ctx, "sub")
        assert type(built) is ClaudeCodeChat and (built.model, built._timeout) == ("sonnet", 30.0)
        assert model_profile_rows(ctx)[1]["key_state"] == "none"
        ctx.config = replace(ctx.config, allow_claude_code=False)
        with pytest.raises(ModelUnavailable, match="allow_claude_code"):
            model_for(ctx, "sub")


@pytest.mark.parametrize("mode, ok, says", [
    ("good", True, None),
    ("logged_out", False, "not logged in"),
    ("garbage", False, "not the JSON"),
])
async def test_the_test_button_runs_the_cli_the_app_finds(tmp_path, monkeypatch, mode, ok, says):
    monkeypatch.setenv(BIN_ENV, str(shim(tmp_path, mode)))
    with _ctx(tmp_path, ALLOWED + SUB) as ctx:
        result = await try_model_profile(ctx, "sub")
    assert result.ok is ok and (says is None or says in result.error)


def test_a_claude_code_profile_saves_only_when_allowed_and_drops_a_url(tmp_path):
    with _ctx(tmp_path) as ctx:
        with pytest.raises(InvalidQuery, match="allow_claude_code"):
            save_model_profile(ctx, "sub", {"provider": "claude_code", "model": "sonnet"}, create=True)
        assert "sub" not in load_config(ctx.config_source)[0].models
    with _ctx(tmp_path, ALLOWED) as ctx:
        save_model_profile(ctx, "sub", {"provider": "claude_code", "model": "opus", "base_url": "http://x"}, create=True)
        assert ctx.config.models["sub"].base_url is None and model_for(ctx, "sub").model == "opus"
        text = ctx.config_source.read_text(encoding="utf-8")
        assert text.index("allow_claude_code") < text.index("[models.sub]")


def test_the_flag_applies_live_with_the_profiles(tmp_path):
    from oracle.services.settings_service import reload_live_settings

    with _ctx(tmp_path) as ctx:
        ctx.config_source.write_text(ctx.config_source.read_text(encoding="utf-8") + ALLOWED + SUB, encoding="utf-8")
        reload_live_settings(ctx)
        assert ctx.config.allow_claude_code and "claude_code" in provider_defaults(ctx)
        assert model_profile_rows(ctx)[1]["off"] is False


def test_turning_the_flag_off_live_keeps_the_profile_but_refuses_it(tmp_path):
    from oracle.services.settings_service import reload_live_settings

    with _ctx(tmp_path, ALLOWED + SUB) as ctx:
        text = ctx.config_source.read_text(encoding="utf-8")
        ctx.config_source.write_text(text.replace("allow_claude_code = true", "allow_claude_code = false"),
                                     encoding="utf-8")
        reload_live_settings(ctx)
        assert not ctx.config.allow_claude_code and "claude_code" not in provider_defaults(ctx)
        assert [(r["name"], r["off"]) for r in model_profile_rows(ctx)] == [("default", False), ("sub", True)]
        with pytest.raises(ModelUnavailable, match="claude_code is off"):
            model_for(ctx, "sub")
        with pytest.raises(InvalidQuery, match="allow_claude_code"):
            save_model_profile(ctx, "sub", {"provider": "claude_code", "model": "opus"}, create=False)
        delete_model_profile(ctx, "sub")


async def test_a_restart_with_the_flag_off_loads_and_the_test_reports_it_off(tmp_path):
    with _ctx(tmp_path, SUB) as ctx:
        assert ctx.config.models["sub"].provider == "claude_code" and not ctx.config.allow_claude_code
        result = await try_model_profile(ctx, "sub")
    assert not result.ok and "claude_code is off" in result.error
