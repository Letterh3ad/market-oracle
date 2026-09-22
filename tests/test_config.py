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
