import pytest

from oracle.config import Config, load_config, setting_list
from oracle.services.context import load_settings, open_context
from oracle.services.errors import ConfigInUse, InvalidQuery
from oracle.services.settings_service import set_setting, settings_form

START = '# mine\n[schedule]\ntime = "22:30"   # keep this comment\n\n[keys]\neodhd = "secret-value"\n'


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"\n' + START, encoding="utf-8")
    config, source = load_settings(path)
    with open_context(config, source) as c:
        yield c


def row(ctx, section, key):
    return next(r for r in settings_form(ctx) if (r["section"], r["key"]) == (section, key))


def test_every_key_is_listed_with_its_value_default_meaning_and_whether_it_applies_live(ctx):
    rows = settings_form(ctx)
    assert [(r["section"], r["key"]) for r in rows] == [(s.section, s.key) for s in setting_list()]
    assert all(r["meaning"] for r in rows)
    peers = row(ctx, "analysts", "peers")
    assert (peers["value"], peers["default"], peers["kind"], peers["live"]) == (20, 20, "int", True)
    assert row(ctx, "schedule", "time")["value"] == "22:30" and row(ctx, "schedule", "time")["live"]
    assert row(ctx, "llm", "model")["live"] is False


def test_a_change_is_saved_keeping_comments_and_live_keys_apply_at_once(ctx):
    path = ctx.config_source
    assert set_setting(ctx, "schedule", "time", "21:15")["value"] == "21:15"
    assert ctx.config.schedule.time == "21:15"
    set_setting(ctx, "llm", "max_steps", 12)
    assert ctx.config.llm.max_steps == 16, "restart keys wait for Restart"
    set_setting(ctx, "sources.web", "max_retries", 2)
    set_setting(ctx, "", "base_currency", "USD")
    text = path.read_text(encoding="utf-8")
    assert "# mine" in text and 'time = "21:15"   # keep this comment' in text
    config = load_config(path)[0]
    assert (config.llm.max_steps, config.sources["web"].max_retries, config.base_currency) == (12, 2, "USD")


def test_a_bad_value_is_refused_with_the_loaders_message_and_nothing_is_written(ctx):
    before = ctx.config_source.read_bytes()
    with pytest.raises(InvalidQuery, match=r"llm.max_steps must be a whole number from 1 to 50"):
        set_setting(ctx, "llm", "max_steps", 0)
    with pytest.raises(InvalidQuery, match="no setting"):
        set_setting(ctx, "llm", "nope", 1)
    assert ctx.config_source.read_bytes() == before


def test_secrets_are_never_listed_only_whether_set_and_can_be_replaced_or_cleared(ctx, monkeypatch):
    monkeypatch.delenv("ORACLE_EODHD_KEY", raising=False)
    assert "secret-value" not in repr(settings_form(ctx))
    key = row(ctx, "keys", "eodhd")
    assert (key["secret"], key["set"], key["from_env"], "value" in key, key["default"]) == (
        True, True, False, False, None)
    assert key["env"] == "ORACLE_EODHD_KEY"
    replaced = set_setting(ctx, "keys", "eodhd", "new-secret")
    assert "new-secret" not in repr(replaced) and replaced["set"]
    assert load_config(ctx.config_source)[0].keys.get("eodhd") == "new-secret"
    assert set_setting(ctx, "keys", "eodhd", None)["set"] is False
    monkeypatch.setenv("ORACLE_EODHD_KEY", "from-env")
    assert row(ctx, "keys", "eodhd")["from_env"] is True


def test_without_a_config_file_defaults_are_listed_and_a_save_makes_one(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None) as ctx:
        assert row(ctx, "risk", "account")["value"] == 10_000.0
        set_setting(ctx, "risk", "account", 5000)
    assert load_config(tmp_path / "oracle.toml")[0].risk.account == 5000.0


def test_a_config_file_another_program_holds_is_a_readable_error_and_is_left_as_it_was(ctx, monkeypatch):
    before = ctx.config_source.read_bytes()

    def locked(src, dst):
        raise PermissionError(13, "Access is denied", str(dst))

    monkeypatch.setattr("oracle.services.settings_service.os.replace", locked)
    with pytest.raises(ConfigInUse, match="oracle.toml is open in another program: close it and try again"):
        set_setting(ctx, "schedule", "time", "21:15")
    assert ctx.config_source.read_bytes() == before
    assert [p.name for p in ctx.config_source.parent.iterdir() if p.suffix == ".new"] == []


def test_llm_model_keys_say_they_are_ignored_once_a_default_profile_exists(ctx):
    from oracle.services.models_service import save_model_profile

    assert "ignored" not in row(ctx, "llm", "model")
    save_model_profile(ctx, "default", {"provider": "ollama", "model": "gemma3:12b"}, create=False)
    assert "[models.default] replaces it" in row(ctx, "llm", "model")["ignored"]
    assert "ignored" not in row(ctx, "llm", "max_steps")


def test_a_blank_midday_time_turns_the_midday_pass_off(ctx):
    assert row(ctx, "schedule", "midday_time")["blank_off"] is True and "blank_off" not in row(ctx, "schedule", "time")
    assert set_setting(ctx, "schedule", "midday_time", "")["value"] == ""
    assert ctx.config.schedule.midday_time == ""


def test_a_midday_time_equal_to_the_tick_time_is_refused_on_write(ctx):
    from oracle.services.settings_service import set_schedule

    before = ctx.config_source.read_bytes()
    with pytest.raises(InvalidQuery, match="midday_time must differ"):
        set_setting(ctx, "schedule", "midday_time", "22:30")
    with pytest.raises(InvalidQuery, match="midday_time must differ"):
        set_schedule(ctx, True, "12:30")
    assert ctx.config_source.read_bytes() == before


def test_fill_times_are_a_list_row_applied_live_and_a_clash_is_refused_on_write(ctx):
    assert row(ctx, "schedule", "fill_times")["kind"] == "list"
    assert set_setting(ctx, "schedule", "fill_times", ["15:15", "09:00"])["value"] == ["15:15", "09:00"]
    assert ctx.config.schedule.fill_times == ("09:00", "15:15")
    before = ctx.config_source.read_bytes()
    with pytest.raises(InvalidQuery, match="fill_times: 22:15 is within 30 minutes of the tick"):
        set_setting(ctx, "schedule", "fill_times", ["22:15"])
    assert ctx.config_source.read_bytes() == before
    assert set_setting(ctx, "schedule", "fill_times", [])["value"] == [] and ctx.config.schedule.fill_times == ()


def test_a_slot_is_a_row_choosing_a_profile_that_applies_at_once(ctx):
    path = ctx.config_source
    path.write_text(path.read_text(encoding="utf-8") + '\n[models.big]\nprovider = "ollama"\nmodel = "qwen3:8b"\n',
                    encoding="utf-8")
    notes = row(ctx, "models.slots", "notes")
    assert (notes["value"], notes["choices"], notes["live"]) == (None, ["default", "big"], True)
    assert set_setting(ctx, "models.slots", "notes", "big")["value"] == "big"
    assert ctx.config.model_slots == {"notes": "big"}
    with pytest.raises(InvalidQuery, match="names no profile 'huge'"):
        set_setting(ctx, "models.slots", "verdict", "huge")
    set_setting(ctx, "models.slots", "notes", None)
    assert ctx.config.model_slots == {}


def test_a_dangling_slot_edited_in_by_hand_is_dropped_on_reload_and_the_rest_still_applies(ctx, caplog):
    from oracle.services.settings_service import reload_live_settings

    set_setting(ctx, "models.slots", "notes", "default")
    path = ctx.config_source
    text = path.read_text(encoding="utf-8").replace('notes = "default"', 'notes = "huge"')
    path.write_text(text + '\n[automation]\nenabled = false\n', encoding="utf-8")
    reload_live_settings(ctx)
    assert ctx.config.model_slots == {} and ctx.config.automation.enabled is False
    assert "models.slots.notes names no profile 'huge'" in caplog.text
    with pytest.raises(InvalidQuery, match="names no profile 'huge'"):
        set_setting(ctx, "models.slots", "verdict", "huge")
