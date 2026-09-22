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
