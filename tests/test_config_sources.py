import pytest

from oracle.config import ConfigError, load_config


def _load(tmp_path, text):
    path = tmp_path / "oracle.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path, cwd=tmp_path)[0]


def test_sources_default_to_the_built_in_set(tmp_path):
    config, _ = load_config(None, cwd=tmp_path)
    assert {"yfinance", "stooq", "gdelt", "google_news", "ddgs", "sec_edgar"} <= set(config.sources)


def test_a_source_override_changes_only_what_it_sets(tmp_path):
    config = _load(tmp_path, "[sources.gdelt]\nrate_per_second = 0.1\n")
    assert config.sources["gdelt"].rate_per_second == 0.1
    assert config.sources["gdelt"].cooldown_s == 900.0
    assert config.sources["yfinance"].rate_per_second == 0.5


def test_a_new_source_needs_a_rate(tmp_path):
    with pytest.raises(ConfigError, match="eodhd"):
        _load(tmp_path, "[sources.eodhd]\ncooldown_s = 60\n")


def test_a_non_positive_rate_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="rate_per_second"):
        _load(tmp_path, "[sources.gdelt]\nrate_per_second = 0\n")


def test_an_unknown_source_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="rate"):
        _load(tmp_path, "[sources.gdelt]\nrate = 1\n")


def test_a_fractional_retry_count_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="max_retries"):
        _load(tmp_path, "[sources.gdelt]\nmax_retries = 2.5\n")
