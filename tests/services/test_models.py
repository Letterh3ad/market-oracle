import httpx
import pytest

from oracle.config import CLAUDE_CONTEXT, PROVIDER_DEFAULTS, load_config
from oracle.research.claude import ClaudeChat
from oracle.research.ollama import OllamaChat
from oracle.services import models_service
from oracle.services.context import load_settings, open_context
from oracle.services.errors import InvalidQuery, ModelUnavailable, UnknownModelProfile
from oracle.services.models_service import (
    context_tokens, delete_model_profile, model_for, model_profile_rows, save_model_profile, try_model_profile,
)
from tests.fakes.ollama import FakeOllama


def _ctx(tmp_path, text="", transport=None):
    path = tmp_path / "oracle.toml"
    path.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"\n{text}', encoding="utf-8")
    config, source = load_settings(path)
    return open_context(config, source, transport=transport)


def test_every_provider_has_a_builder():
    assert set(models_service.BUILDERS) == set(PROVIDER_DEFAULTS)


def test_a_toml_with_only_llm_yields_the_same_model_as_before(tmp_path):
    with _ctx(tmp_path, '[llm]\nmodel = "qwen3:8b"\nbase_url = "http://gpu:11434"\ntimeout_s = 60\ncontext = 8192\n') as ctx:
        llm = ctx.config.llm
        before = OllamaChat(llm.model, llm.base_url, llm.timeout_s, llm.context, transport=ctx.transport)
        built = model_for(ctx, "default")
        assert type(built) is OllamaChat and vars(built) == vars(before)
        assert context_tokens(ctx, "default") == 8192
    with _ctx(tmp_path, '[llm]\nbackend = "claude"\nclaude_model = "claude-sonnet-5"\n') as ctx:
        built = model_for(ctx, "default")
        assert type(built) is ClaudeChat and vars(built) == vars(ClaudeChat("claude-sonnet-5", 180.0))
        assert context_tokens(ctx, "default") == CLAUDE_CONTEXT


def test_a_profile_builds_its_provider_with_its_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_CLAUDE", "sk-env")
    with _ctx(tmp_path, '[models.deep]\nprovider = "claude_api"\nmodel = "claude-opus-5"\nkey_env = "MY_CLAUDE"\n'
                        'context = 100000\n[models.lit]\nprovider = "claude_api"\nmodel = "m"\nkey = "sk-lit"\n'
                        '[models.gone]\nprovider = "claude_api"\nmodel = "m"\nkey_env = "NOT_SET_ANYWHERE"\n') as ctx:
        assert vars(model_for(ctx, "deep")) == vars(ClaudeChat("claude-opus-5", 180.0, api_key="sk-env"))
        assert context_tokens(ctx, "deep") == 100000
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
    assert (default["key_set"], deep["key_set"], lit["key_set"], gone["key_set"]) == (False, True, True, False)
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
        assert model_for(ctx, "big").model == "qwen3:8b" and context_tokens(ctx, "big") == 8192
        save_model_profile(ctx, "big", {"provider": "ollama", "model": "qwen3:14b"}, create=False)
        assert context_tokens(ctx, "big") == 16384, "an edit replaces the profile; unset keys take defaults"
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
