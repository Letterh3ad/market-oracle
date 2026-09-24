import pytest

from oracle.config import CLAUDE_CONTEXT, ConfigError, ModelProfile, load_config, model_profiles


def _load(tmp_path, text):
    path = tmp_path / "oracle.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path, cwd=tmp_path)[0]


def test_without_profiles_the_llm_section_is_the_default_profile(tmp_path):
    config = _load(tmp_path, '[llm]\nmodel = "qwen3:8b"\nbase_url = "http://gpu:11434"\ntimeout_s = 60\n'
                             "context = 8192\nmax_steps = 12\n")
    assert model_profiles(config) == {"default": ModelProfile(
        provider="ollama", model="qwen3:8b", base_url="http://gpu:11434", context=8192, timeout_s=60.0)}


def test_the_claude_backend_maps_to_a_claude_api_default_with_claudes_context(tmp_path):
    config = _load(tmp_path, '[llm]\nbackend = "claude"\nclaude_model = "claude-sonnet-5"\ncontext = 8192\n')
    assert model_profiles(config)["default"] == ModelProfile(
        provider="claude_api", model="claude-sonnet-5", base_url=None, context=CLAUDE_CONTEXT, timeout_s=180.0)


def test_profiles_take_provider_defaults_and_a_default_profile_overrides_llm(tmp_path):
    config = _load(tmp_path, '[llm]\nmodel = "qwen3:8b"\nmax_steps = 9\n'
                             '[models.default]\nprovider = "ollama"\nmodel = "gemma3:12b"\n'
                             '[models.deep]\nprovider = "claude_api"\nmodel = "claude-opus-5"\nkey_env = "MY_KEY"\n')
    profiles = model_profiles(config)
    assert list(profiles) == ["default", "deep"]
    assert profiles["default"] == ModelProfile("ollama", "gemma3:12b", "http://localhost:11434", 16384, 180.0)
    assert profiles["deep"] == ModelProfile("claude_api", "claude-opus-5", None, CLAUDE_CONTEXT, 180.0, key_env="MY_KEY")
    assert config.llm.max_steps == 9


def test_a_literal_key_is_kept_but_never_shown_in_a_repr(tmp_path):
    config = _load(tmp_path, '[models.deep]\nprovider = "claude_api"\nmodel = "claude-opus-5"\nkey = "sk-secret"\n')
    assert config.models["deep"].key == "sk-secret"
    assert "sk-secret" not in repr(config)


@pytest.mark.parametrize("table, message", [
    ('provider = "gpt"\nmodel = "x"', r"models.fast.provider must be one of ollama, claude_api, openai_compatible, got 'gpt'"),
    ('model = "x"', r"models.fast needs a provider"),
    ('provider = "ollama"', r"models.fast needs a model"),
    ('provider = "ollama"\nmodel = ""', r"models.fast.model must be a model name"),
    ('provider = "ollama"\nmodel = "x"\ncontext = 100', r"models.fast.context must be a whole number of tokens"),
    ('provider = "ollama"\nmodel = "x"\ncontext = true', r"models.fast.context must be a whole number of tokens"),
    ('provider = "ollama"\nmodel = "x"\ntimeout_s = 0', r"models.fast.timeout_s must be a positive number"),
    ('provider = "ollama"\nmodel = "x"\nbase_url = "gpu"', r"models.fast.base_url must be an http\(s\) URL"),
    ('provider = "claude_api"\nmodel = "x"\nkey_env = "not a name"', r"models.fast.key_env must be the name of an"),
    ('provider = "claude_api"\nmodel = "x"\nkey = ""', r"models.fast.key must be a non-empty string"),
    ('provider = "claude_api"\nmodel = "x"\nkey = "k"\nkey_env = "K"', r"models.fast sets key and key_env"),
    ('provider = "openai_compatible"\nmodel = "x"', r"models.fast needs a base_url: the server's API root"),
    ('provider = "ollama"\nmodel = "x"\nbase_url = "http://gpu:11434/?a=1"', r"models.fast.base_url must be an http\(s\)"),
    ('provider = "ollama"\nmodel = "x"\nbase_url = "http://gpu:11434/#a"', r"models.fast.base_url must be an http\(s\)"),
    ('provider = "ollama"\nmodel = "x"\nbase_url = "http://u:p@gpu:11434"', r"models.fast.base_url must be an http\(s\)"),
    ('provider = "openai_compatible"\nmodel = "x"\nbase_url = "http://gpu.lan:8000/v1"\nkey_env = "K"',
     r"models.fast would send its key unencrypted to gpu.lan: use https://"),
    ('provider = "ollama"\nmodel = "x"\ntemperature = 1', r"unknown key\(s\) in \[models.fast\]: temperature"),
])
def test_a_bad_profile_is_refused_naming_the_key(tmp_path, table, message):
    with pytest.raises(ConfigError, match=message):
        _load(tmp_path, f"[models.fast]\n{table}\n")


def test_a_provider_that_is_not_a_string_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match=r"models.fast.provider must be one of"):
        _load(tmp_path, '[models.fast]\nprovider = ["ollama"]\nmodel = "x"\n')


@pytest.mark.parametrize("value", ["sk-ant-api03-abcDEF", "AKIAIOSFODNN7EXAMPLE", "abcdefghijKLMNOPQRSTuvwxyz123",
                                   "sk_live_abc", "x" * 65])
def test_a_key_pasted_into_key_env_is_refused_without_echoing_it(tmp_path, value):
    with pytest.raises(ConfigError, match="not a key") as caught:
        _load(tmp_path, f'[models.deep]\nprovider = "claude_api"\nmodel = "m"\nkey_env = "{value}"\n')
    assert value not in str(caught.value)


def test_an_ordinary_variable_name_is_a_fine_key_env(tmp_path):
    for name in ("ANTHROPIC_API_KEY", "my_claude_key", "Work_Key"):
        _load(tmp_path, f'[models.deep]\nprovider = "claude_api"\nmodel = "m"\nkey_env = "{name}"\n')


@pytest.mark.parametrize("line", ['key = "k"', 'key_env = "K"'])
def test_a_keyless_provider_refuses_a_key(tmp_path, line):
    with pytest.raises(ConfigError, match="ollama takes no key"):
        _load(tmp_path, f'[models.fast]\nprovider = "ollama"\nmodel = "x"\n{line}\n')


def test_unset_keys_stay_unset_as_parsed_and_resolve_from_the_provider(tmp_path):
    config = _load(tmp_path, '[models.fast]\nprovider = "ollama"\nmodel = "x"\n')
    assert config.models["fast"] == ModelProfile("ollama", "x")
    assert model_profiles(config)["fast"] == ModelProfile("ollama", "x", "http://localhost:11434", 16384, 180.0)


def test_removing_a_table_keeps_the_comments_above_the_next_one_and_every_other_section():
    from oracle.config import without_section

    text = ('# top\n[llm]\nmax_steps = 9\n\n[models.big]\nprovider = "ollama"\nmodel = "x"\n\n'
            '# about the next one\n# second line\n[models.small]\nprovider = "ollama"\nmodel = "y"\n\n[keys]\n')
    assert without_section(text, "models.big") == (
        '# top\n[llm]\nmax_steps = 9\n\n# about the next one\n# second line\n[models.small]\nprovider = "ollama"\n'
        'model = "y"\n\n[keys]\n')
    assert without_section(text, "models.small").endswith('model = "x"\n\n# about the next one\n# second line\n[keys]\n')
    assert without_section(text, "models.none") == text


def test_removing_a_profile_written_inline_is_refused_rather_than_left_behind():
    from oracle.config import without_section

    with pytest.raises(ConfigError, match="not a plain table"):
        without_section('models = { big = { provider = "ollama", model = "x" } }\n', "models.big")


def test_profile_names_are_checked_and_may_not_differ_only_in_case(tmp_path):
    with pytest.raises(ConfigError, match="models: Fast and fast differ only in case"):
        _load(tmp_path, '[models.Fast]\nprovider = "ollama"\nmodel = "x"\n[models.fast]\nprovider = "ollama"\nmodel = "y"\n')
    with pytest.raises(ConfigError, match="profile name"):
        _load(tmp_path, '[models."a b"]\nprovider = "ollama"\nmodel = "x"\n')
    with pytest.raises(ConfigError, match="models must be"):
        _load(tmp_path, 'models = 3\n')


def test_a_duplicate_table_is_refused_with_the_toml_parsers_message(tmp_path):
    with pytest.raises(ConfigError, match="not valid TOML"):
        _load(tmp_path, '[models.a]\nprovider = "ollama"\nmodel = "x"\n[models.a]\nprovider = "ollama"\nmodel = "y"\n')


def test_an_openai_compatible_profile_needs_a_base_url_and_may_take_a_key(tmp_path):
    config = _load(tmp_path, '[models.lm]\nprovider = "openai_compatible"\nmodel = "qwen3-8b"\n'
                             'base_url = "http://localhost:1234/v1"\n'
                             '[models.router]\nprovider = "openai_compatible"\nmodel = "m"\n'
                             'base_url = "https://openrouter.ai/api/v1"\nkey_env = "OPENROUTER_KEY"\n')
    profiles = model_profiles(config)
    assert profiles["lm"] == ModelProfile("openai_compatible", "qwen3-8b", "http://localhost:1234/v1", 16384, 180.0)
    assert profiles["router"].key_env == "OPENROUTER_KEY"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_a_key_may_go_over_plain_http_to_this_machine(tmp_path, host):
    _load(tmp_path, f'[models.lm]\nprovider = "openai_compatible"\nmodel = "m"\nbase_url = "http://{host}:1234/v1"\n'
                    'key = "k"\n')

