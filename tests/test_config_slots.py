import pytest

from oracle.config import SLOTS, ConfigError, check_profile_name, load_config, setting_list


def _load(tmp_path, text, strict=True):
    path = tmp_path / "oracle.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path, cwd=tmp_path, strict=strict)[0]


BIG = '[models.big]\nprovider = "ollama"\nmodel = "qwen3:8b"\n'


def test_slots_name_profiles_and_are_not_a_profile(tmp_path):
    config = _load(tmp_path, BIG + '[models.slots]\nnotes = "big"\nverdict = "default"\n')
    assert config.model_slots == {"notes": "big", "verdict": "default"}
    assert "slots" not in config.models


def test_without_slots_none_are_set(tmp_path):
    assert _load(tmp_path, BIG).model_slots == {}


@pytest.mark.parametrize("slots, message", [
    ('reading = "big"', r"unknown slot\(s\) in \[models.slots\]: reading"),
    ('notes = "huge"', r"models.slots.notes names no profile 'huge'"),
    ('notes = 3', r"models.slots.notes names no profile 3"),
])
def test_an_unknown_slot_or_profile_is_refused(tmp_path, slots, message):
    with pytest.raises(ConfigError, match=message):
        _load(tmp_path, BIG + f"[models.slots]\n{slots}\n")


def test_at_start_a_slot_naming_a_missing_profile_runs_on_default_with_a_warning(tmp_path, caplog):
    config = _load(tmp_path, BIG + '[models.slots]\nnotes = "huge"\nverdict = "big"\n', strict=False)
    assert config.model_slots == {"verdict": "big"}
    assert "models.slots.notes names no profile 'huge'" in caplog.text
    with pytest.raises(ConfigError, match="unknown slot"):
        _load(tmp_path, BIG + '[models.slots]\nreading = "big"\n', strict=False)


def test_slots_is_reserved_as_a_profile_name():
    with pytest.raises(ConfigError, match="reserved"):
        check_profile_name("Slots")


def test_slots_is_no_top_level_key(tmp_path):
    with pytest.raises(ConfigError, match="unknown key"):
        _load(tmp_path, "model_slots = 1\n")


def test_every_slot_is_a_settings_row():
    rows = [(s.section, s.key, s.kind, s.default) for s in setting_list() if s.section == "models.slots"]
    assert rows == [("models.slots", slot, "str", None) for slot in SLOTS]
    assert SLOTS == ("research", "notes", "roles", "queries", "verdict", "discovery", "ai_account")
