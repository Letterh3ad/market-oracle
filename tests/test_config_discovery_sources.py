import pytest

from oracle.config import ConfigError, load_config, setting_list


def test_every_source_is_on_and_there_are_no_themes_by_default(tmp_path):
    d = load_config(None, cwd=tmp_path)[0].discovery
    assert (d.search_related, d.search_themes, d.search_movers, d.themes) == (True, True, True, ())


def test_themes_are_trimmed_and_the_switches_change(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text('[discovery]\nthemes = [" AI chips ", "defence"]\nsearch_movers = false\n', encoding="utf-8")
    d = load_config(path, cwd=tmp_path)[0].discovery
    assert (d.themes, d.search_movers) == (("AI chips", "defence"), False)


def test_themes_edit_as_a_list_in_the_settings_form():
    [row] = [s for s in setting_list() if (s.section, s.key) == ("discovery", "themes")]
    assert (row.kind, row.default) == ("list", [])


@pytest.mark.parametrize("line", [
    'themes = "AI chips"', 'themes = [""]', "themes = [3]", f'themes = ["{"x" * 61}"]', "search_themes = 1",
    "search_related = \"yes\"", "search_movers = 0",
])
def test_bad_source_settings_are_refused_by_name(tmp_path, line):
    path = tmp_path / "oracle.toml"
    path.write_text(f"[discovery]\n{line}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="discovery"):
        load_config(path, cwd=tmp_path)
