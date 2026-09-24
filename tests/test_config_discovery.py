import pytest

from oracle.config import ConfigError, load_config


def test_discovery_is_on_adding_one_a_run_up_to_ten_and_never_tesla(tmp_path):
    d = load_config(None, cwd=tmp_path)[0].discovery
    assert (d.enabled, d.per_tick, d.max_total, d.never) == (True, 1, 10, ("Tesla",))


def test_discovery_settings_can_change(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text('[discovery]\nenabled = false\nper_tick = 3\nmax_total = 25\nnever = ["Tesla", " Nikola "]\n',
                    encoding="utf-8")
    d = load_config(path, cwd=tmp_path)[0].discovery
    assert (d.enabled, d.per_tick, d.max_total, d.never) == (False, 3, 25, ("Tesla", "Nikola"))


@pytest.mark.parametrize("line", [
    "enabled = 1", "per_tick = 0", "per_tick = 21", "per_tick = true", "max_total = 0", "max_total = 501",
    "never = \"Tesla\"", "never = [\"\"]", "never = [3]", "sources = 1",
])
def test_bad_discovery_values_are_refused_by_name(tmp_path, line):
    path = tmp_path / "oracle.toml"
    path.write_text(f"[discovery]\n{line}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="discovery"):
        load_config(path, cwd=tmp_path)
