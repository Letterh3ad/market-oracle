from pathlib import Path

import pytest

from oracle.config import ConfigError, load_config


def test_no_path_and_no_default_file_gives_defaults_and_no_source(tmp_path):
    config, source = load_config(None, cwd=tmp_path)
    assert source is None
    assert config.base_currency == "GBP"
    assert config.db_path == tmp_path / "oracle.db"


def test_partial_file_overrides_only_what_it_sets(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text('base_currency = "EUR"\n', encoding="utf-8")
    config, source = load_config(path, cwd=tmp_path)
    assert source == path
    assert config.base_currency == "EUR"
    assert config.db_path == tmp_path / "oracle.db"


def test_relative_db_path_resolves_against_the_config_file_not_the_cwd(tmp_path):
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    path = conf_dir / "oracle.toml"
    path.write_text('db_path = "data/o.db"\n', encoding="utf-8")
    config, _ = load_config(path, cwd=tmp_path / "elsewhere")
    assert config.db_path == conf_dir / "data" / "o.db"


def test_unknown_key_is_rejected_by_name(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text('base_curency = "EUR"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="base_curency"):
        load_config(path, cwd=tmp_path)


def test_wrong_type_is_rejected(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text("base_currency = 5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="base_currency"):
        load_config(path, cwd=tmp_path)


def test_currency_must_be_an_iso_code(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text('base_currency = "pounds"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="base_currency"):
        load_config(path, cwd=tmp_path)


def test_example_file_documents_every_key():
    from dataclasses import fields

    from oracle.config import Config

    example = Path(__file__).resolve().parents[1] / "oracle.toml.example"
    config, _ = load_config(example, cwd=example.parent)
    text = example.read_text(encoding="utf-8")
    assert all(f"{f.name} =" in text or f"[{f.name}." in text or f"[{f.name}]" in text for f in fields(Config))
    assert isinstance(config, Config)


def test_an_oracle_toml_in_the_cwd_is_found_without_a_path(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text('base_currency = "USD"\n', encoding="utf-8")
    config, source = load_config(None, cwd=tmp_path)
    assert source == path
    assert config.base_currency == "USD"


def test_analysts_peers_defaults_to_20_and_can_be_changed(tmp_path):
    assert load_config(None, cwd=tmp_path)[0].analysts.peers == 20
    path = tmp_path / "oracle.toml"
    path.write_text("[analysts]\npeers = 40\n", encoding="utf-8")
    assert load_config(path, cwd=tmp_path)[0].analysts.peers == 40


@pytest.mark.parametrize("value", ["4", "101", "20.5", '"20"', "true"])
def test_analysts_peers_outside_5_to_100_is_refused(tmp_path, value):
    path = tmp_path / "oracle.toml"
    path.write_text(f"[analysts]\npeers = {value}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="analysts.peers"):
        load_config(path, cwd=tmp_path)


def test_risk_defaults_are_the_design_rules_and_can_be_changed(tmp_path):
    risk = load_config(None, cwd=tmp_path)[0].risk
    assert (risk.account, risk.max_position, risk.cash_floor, risk.target_volatility) == (10_000.0, 0.10, 0.20, 0.20)
    path = tmp_path / "oracle.toml"
    path.write_text("[risk]\nmax_position = 0.05\n", encoding="utf-8")
    assert load_config(path, cwd=tmp_path)[0].risk.max_position == 0.05


@pytest.mark.parametrize("line", ["max_position = 0", "cash_floor = 1.0", "target_volatility = -0.1", "account = 0",
                                  'account = "lots"'])
def test_risk_values_out_of_range_are_refused(tmp_path, line):
    path = tmp_path / "oracle.toml"
    path.write_text(f"[risk]\n{line}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="risk."):
        load_config(path, cwd=tmp_path)


def test_strategy_rules_default_to_the_design_and_are_validated(tmp_path):
    s = load_config(None, cwd=tmp_path)[0].strategy
    assert (s.min_probability, s.max_positions, s.stop_loss) == (0.6, 10, 0.08)
    path = tmp_path / "oracle.toml"
    for line in ("max_positions = 0", "stop_loss = 1.5", "min_probability = 0", "max_positions = 2.5"):
        path.write_text(f"[strategy]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="strategy."):
            load_config(path, cwd=tmp_path)


def test_the_gui_listens_on_localhost_by_default_and_the_port_can_change(tmp_path):
    gui = load_config(None, cwd=tmp_path)[0].gui
    assert (gui.host, gui.port) == ("127.0.0.1", 8740)
    path = tmp_path / "oracle.toml"
    path.write_text("[gui]\nport = 9000\n", encoding="utf-8")
    assert load_config(path, cwd=tmp_path)[0].gui.port == 9000
    for line in ("port = 80", "port = 70000", 'port = "9000"', "host = 5", "colour = 1"):
        path.write_text(f"[gui]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="gui"):
            load_config(path, cwd=tmp_path)


def test_with_setting_replaces_a_value_and_keeps_comments_and_other_sections():
    from oracle.config import with_setting

    text = "# mine\n[other]\npeers = 3\n\n[analysts]\npeers = 20   # how many to compare\n[risk]\naccount = 5000.0\n"
    assert with_setting(text, "analysts", "peers", "40") == (
        "# mine\n[other]\npeers = 3\n\n[analysts]\npeers = 40   # how many to compare\n[risk]\naccount = 5000.0\n"
    )


def test_with_setting_adds_the_key_or_the_section_when_missing():
    from oracle.config import with_setting

    assert with_setting("[analysts]\n[risk]\naccount = 1.0\n", "analysts", "peers", "30") == (
        "[analysts]\npeers = 30\n[risk]\naccount = 1.0\n"
    )
    assert with_setting('db_path = "o.db"', "analysts", "peers", "30") == 'db_path = "o.db"\n\n[analysts]\npeers = 30\n'
    assert with_setting("", "analysts", "peers", "30") == "[analysts]\npeers = 30\n"


def test_research_runs_on_local_ollama_by_default_and_each_llm_setting_can_change(tmp_path):
    llm = load_config(None, cwd=tmp_path)[0].llm
    assert (llm.backend, llm.model, llm.base_url) == ("ollama", "qwen3:4b-instruct-2507-q8_0", "http://localhost:11434")
    assert (llm.max_steps, llm.timeout_s, llm.context) == (8, 180.0, 8192)
    path = tmp_path / "oracle.toml"
    path.write_text('[llm]\nmodel = "qwen3:8b"\nmax_steps = 12\ntimeout_s = 60\n', encoding="utf-8")
    llm = load_config(path, cwd=tmp_path)[0].llm
    assert (llm.model, llm.max_steps, llm.timeout_s) == ("qwen3:8b", 12, 60.0)
    for line in ('backend = "gpt"', 'model = ""', "max_steps = 0", "max_steps = 51", "timeout_s = 0",
                 "context = 512", 'base_url = "localhost"', "temperature = 1"):
        path.write_text(f"[llm]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="llm"):
            load_config(path, cwd=tmp_path)


def test_a_contact_for_sec_is_unset_by_default_and_must_hold_an_email(tmp_path):
    assert load_config(None, cwd=tmp_path)[0].research.contact is None
    path = tmp_path / "oracle.toml"
    path.write_text('[research]\ncontact = "Faye faye@example.com"\n', encoding="utf-8")
    assert load_config(path, cwd=tmp_path)[0].research.contact == "Faye faye@example.com"
    for line in ('contact = "Faye"', "contact = 5", 'contact = ""', 'email = "a@b.c"'):
        path.write_text(f"[research]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="research"):
            load_config(path, cwd=tmp_path)


def test_claude_can_be_chosen_with_its_own_model_setting(tmp_path):
    assert load_config(None, cwd=tmp_path)[0].llm.claude_model == "claude-opus-5"
    path = tmp_path / "oracle.toml"
    path.write_text('[llm]\nbackend = "claude"\nclaude_model = "claude-sonnet-5"\n', encoding="utf-8")
    llm = load_config(path, cwd=tmp_path)[0].llm
    assert (llm.backend, llm.claude_model) == ("claude", "claude-sonnet-5")
    path.write_text('[llm]\nclaude_model = ""\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="llm.claude_model"):
        load_config(path, cwd=tmp_path)
