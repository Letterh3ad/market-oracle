from dataclasses import asdict

import pytest

from oracle.broker.regime import PRESETS, RegimeSettings, settings_from
from oracle.config import Config, ConfigError, load_config


def load(tmp_path, text):
    path = tmp_path / "oracle.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path, cwd=tmp_path)[0]


def test_the_defaults_are_the_books_presets():
    settings = settings_from(asdict(Config().regime), max_positions=10, min_probability=0.6)
    assert settings == RegimeSettings(weights={m: dict(r) for m, r in PRESETS.items()})


def test_a_regime_table_overrides_weights_and_knobs(tmp_path):
    config = load(tmp_path, "[regime]\ncalm_up_trend = 0.4\nsmoothing_span = 10\ntilt_cap = 0.2\n")
    assert (config.regime.calm_up_trend, config.regime.smoothing_span, config.regime.tilt_cap) == (0.4, 10, 0.2)
    assert config.regime.calm_up_momentum == 0.25


@pytest.mark.parametrize(("line", "message"), [
    ("calm_up_trend = 1.5", "regime.calm_up_trend must be 0 to 1"),
    ("calm_up_trend = true", "regime.calm_up_trend must be 0 to 1"),
    ("smoothing_span = 0", "regime.smoothing_span must be 1 to 60"),
    ("smoothing_span = 2.5", "regime.smoothing_span must be 1 to 60"),
    ("sector_share = 0", "regime.sector_share must be above 0"),
    ("tilt_cap = 1", "regime.tilt_cap must be at least 0 and below 1"),
    ("good_factor = 0.9", "regime.good_factor must be 1 to 5"),
    ("astrology = 1", "unknown key"),
])
def test_bad_regime_values_are_refused(tmp_path, line, message):
    with pytest.raises(ConfigError, match=message):
        load(tmp_path, f"[regime]\n{line}\n")


def test_a_mood_with_every_weight_zero_is_refused(tmp_path):
    zeros = "\n".join(f"calm_down_{m} = 0" for m in PRESETS["calm_down"])
    with pytest.raises(ConfigError, match="every calm_down_ weight is 0"):
        load(tmp_path, f"[regime]\n{zeros}\n")
