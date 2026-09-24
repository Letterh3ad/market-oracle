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


def test_compare_benchmarks_default_to_two_vanguard_funds_and_can_be_changed(tmp_path):
    assert load_config(None, cwd=tmp_path)[0].compare.benchmarks == ("VWRL.L", "VUSA.L")
    path = tmp_path / "oracle.toml"
    path.write_text('[compare]\nbenchmarks = ["SEGA.L"]\n', encoding="utf-8")
    assert load_config(path, cwd=tmp_path)[0].compare.benchmarks == ("SEGA.L",)
    path.write_text("[compare]\nbenchmarks = []\n", encoding="utf-8")
    assert load_config(path, cwd=tmp_path)[0].compare.benchmarks == ()


@pytest.mark.parametrize("line", ["benchmarks = 1", 'benchmarks = ["ok", 2]', 'benchmarks = ["ok", ""]'])
def test_compare_benchmarks_must_be_a_list_of_symbols(tmp_path, line):
    path = tmp_path / "oracle.toml"
    path.write_text(f"[compare]\n{line}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="compare.benchmarks"):
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


def test_with_settings_replaces_a_value_and_keeps_comments_and_other_sections():
    from oracle.config import with_settings

    text = "# mine\n[other]\npeers = 3\n\n[analysts]\npeers = 20   # how many to compare\n[risk]\naccount = 5000.0\n"
    assert with_settings(text, "analysts", {"peers": 40}) == (
        "# mine\n[other]\npeers = 3\n\n[analysts]\npeers = 40   # how many to compare\n[risk]\naccount = 5000.0\n"
    )


def test_with_settings_adds_the_key_or_the_section_when_missing():
    from oracle.config import with_settings

    assert with_settings("[analysts]\n[risk]\naccount = 1.0\n", "analysts", {"peers": 30}) == (
        "[analysts]\npeers = 30\n[risk]\naccount = 1.0\n"
    )
    assert with_settings('db_path = "o.db"', "analysts", {"peers": 30}) == 'db_path = "o.db"\n[analysts]\npeers = 30\n'
    assert with_settings("", "analysts", {"peers": 30}) == "[analysts]\npeers = 30\n"


def test_research_runs_on_local_ollama_by_default_and_each_llm_setting_can_change(tmp_path):
    llm = load_config(None, cwd=tmp_path)[0].llm
    assert (llm.backend, llm.model, llm.base_url) == ("ollama", "qwen3:4b-instruct-2507-q8_0", "http://localhost:11434")
    assert (llm.max_steps, llm.timeout_s, llm.context) == (16, 180.0, 16384)
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


def test_scheduling_is_off_by_default_at_half_past_ten(tmp_path):
    schedule = load_config(None, cwd=tmp_path)[0].schedule
    assert (schedule.enabled, schedule.time) == (False, "22:30")


def test_schedule_settings_are_checked(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text('[schedule]\nenabled = true\ntime = "07:05"\n', encoding="utf-8")
    schedule = load_config(path, cwd=tmp_path)[0].schedule
    assert (schedule.enabled, schedule.time) == (True, "07:05")
    for line in ('enabled = "yes"', 'time = "24:00"', 'time = "7:05"', "time = 2230", "every = 1"):
        path.write_text(f"[schedule]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="schedule"):
            load_config(path, cwd=tmp_path)


def test_a_tick_warns_10_minutes_ahead_and_research_ignores_idleness_by_default(tmp_path):
    s = load_config(None, cwd=tmp_path)[0].schedule
    assert (s.warn_minutes, s.research_when_idle, s.idle_minutes) == (10, False, 15)
    path = tmp_path / "oracle.toml"
    path.write_text("[schedule]\nwarn_minutes = 0\nresearch_when_idle = true\nidle_minutes = 5\n", encoding="utf-8")
    s = load_config(path, cwd=tmp_path)[0].schedule
    assert (s.warn_minutes, s.research_when_idle, s.idle_minutes) == (0, True, 5)
    for line in ("warn_minutes = -1", "warn_minutes = 121", "warn_minutes = 2.5", "warn_minutes = true",
                 'research_when_idle = "yes"', "idle_minutes = 0", "idle_minutes = 241"):
        path.write_text(f"[schedule]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="schedule"):
            load_config(path, cwd=tmp_path)


def test_automation_is_on_by_default_and_checked(tmp_path):
    assert load_config(None, cwd=tmp_path)[0].automation.enabled is True
    path = tmp_path / "oracle.toml"
    path.write_text("[automation]\nenabled = false\n", encoding="utf-8")
    assert load_config(path, cwd=tmp_path)[0].automation.enabled is False
    for line in ('enabled = "no"', "later = 1"):
        path.write_text(f"[automation]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="automation"):
            load_config(path, cwd=tmp_path)


def test_the_ai_account_is_off_by_default_with_a_week_long_verdict_age(tmp_path):
    ai = load_config(None, cwd=tmp_path)[0].ai_account
    assert (ai.enabled, ai.max_verdict_age_days) == (False, 7)
    path = tmp_path / "oracle.toml"
    path.write_text("[ai_account]\nenabled = true\nmax_verdict_age_days = 3\n", encoding="utf-8")
    ai = load_config(path, cwd=tmp_path)[0].ai_account
    assert (ai.enabled, ai.max_verdict_age_days) == (True, 3)
    for line in ('enabled = "yes"', "max_verdict_age_days = 0", "max_verdict_age_days = 2.5",
                 "max_verdict_age_days = 91", "max_verdict_age_days = true", "later = 1"):
        path.write_text(f"[ai_account]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="ai_account"):
            load_config(path, cwd=tmp_path)


def test_a_run_reads_12_articles_by_default_and_the_count_is_a_setting(tmp_path):
    assert load_config(None, cwd=tmp_path)[0].research.read_articles == 12
    path = tmp_path / "oracle.toml"
    path.write_text("[research]\nread_articles = 5\n", encoding="utf-8")
    assert load_config(path, cwd=tmp_path)[0].research.read_articles == 5
    for line in ("read_articles = 0", "read_articles = 31", "read_articles = true", "read_articles = 2.5"):
        path.write_text(f"[research]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="research.read_articles"):
            load_config(path, cwd=tmp_path)


def test_ai_research_refresh_defaults_to_a_5_percent_move_and_120_minutes(tmp_path):
    ai = load_config(None, cwd=tmp_path)[0].ai_account
    assert (ai.move_threshold, ai.research_budget_min) == (0.05, 120)
    path = tmp_path / "oracle.toml"
    path.write_text("[ai_account]\nmove_threshold = 0.1\nresearch_budget_min = 45\n", encoding="utf-8")
    ai = load_config(path, cwd=tmp_path)[0].ai_account
    assert (ai.move_threshold, ai.research_budget_min) == (0.1, 45)
    for line in ("move_threshold = 0", "move_threshold = 1", "move_threshold = true", "research_budget_min = 0",
                 "research_budget_min = 481", "research_budget_min = 2.5"):
        path.write_text(f"[ai_account]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="ai_account"):
            load_config(path, cwd=tmp_path)


def test_with_settings_reaches_top_level_keys_and_source_tables_and_none_removes_a_key():
    from oracle.config import with_settings

    text = '# head\ndb_path = "o.db"\n\n[keys]\neodhd = "old"  # keep\n'
    out = with_settings(text, "", {"base_currency": "USD"})
    out = with_settings(out, "sources.web", {"max_retries": 2})
    out = with_settings(out, "keys", {"eodhd": None})
    assert out.startswith('# head\ndb_path = "o.db"\nbase_currency = "USD"\n')
    assert "[sources.web]\nmax_retries = 2" in out and "eodhd" not in out
    assert with_settings("", "keys", {"eodhd": None}) == "[keys]\n"


def test_every_setting_is_a_real_key_with_a_one_line_meaning_in_the_example():
    from oracle.config import EXAMPLE_PATH, example_meanings, setting_list

    listed = setting_list()
    meanings = example_meanings(EXAMPLE_PATH.read_text(encoding="utf-8"))
    missing = [f"[{s.section}] {s.key}" for s in listed if not s.meaning_in(meanings)]
    assert missing == []
    assert set(meanings) <= {(s.section.split(".")[0], s.key) for s in listed}, "the example names a key the form lacks"
    sections = {s.section for s in listed}
    assert {"", "sources.yahoo", "sources.gdelt", "prices", "risk", "llm", "research", "keys"} <= sections
    assert {s.key for s in listed if s.section == ""} == {"db_path", "base_currency"}
    by_name = {(s.section, s.key): s for s in listed}
    assert (by_name["risk", "account"].kind, by_name["risk", "account"].default) == ("float", 10_000.0)
    assert by_name["prices", "sources"].default == ["yahoo", "eodhd"]
    assert (by_name["research", "contact"].kind, by_name["research", "contact"].default) == ("str", None)
    assert by_name["keys", "eodhd"].secret and not by_name["llm", "model"].secret


def test_example_meanings_read_inline_comments_including_commented_out_keys():
    from oracle.config import example_meanings

    text = ('[sources.gdelt]\nrate_per_second = 0.1   # requests a second\n'
            '[keys]\n# eodhd = "..."   # eodhd.com prices\n# backend = "claude" runs on Claude: no meaning\n')
    assert example_meanings(text) == {("sources", "rate_per_second"): "requests a second",
                                      ("keys", "eodhd"): "eodhd.com prices"}


def test_suffix_blind_grouping_is_off_by_default_and_checked(tmp_path):
    assert load_config(None, cwd=tmp_path)[0].universe.suffix_blind_grouping is False
    path = tmp_path / "oracle.toml"
    path.write_text("[universe]\nsuffix_blind_grouping = true\n", encoding="utf-8")
    assert load_config(path, cwd=tmp_path)[0].universe.suffix_blind_grouping is True
    for line in ('suffix_blind_grouping = "yes"', "later = 1"):
        path.write_text(f"[universe]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="universe"):
            load_config(path, cwd=tmp_path)


def test_the_midday_pass_is_at_half_past_twelve_for_an_hour_and_empty_turns_it_off(tmp_path):
    s = load_config(None, cwd=tmp_path)[0].schedule
    assert (s.midday_time, s.midday_budget_min) == ("12:30", 60)
    path = tmp_path / "oracle.toml"
    path.write_text('[schedule]\nmidday_time = ""\nmidday_budget_min = 5\n', encoding="utf-8")
    s = load_config(path, cwd=tmp_path)[0].schedule
    assert (s.midday_time, s.midday_budget_min) == ("", 5)
    for line in ('midday_time = "25:00"', "midday_time = 1230", "midday_budget_min = 0",
                 "midday_budget_min = 481", "midday_budget_min = true"):
        path.write_text(f"[schedule]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="schedule.midday"):
            load_config(path, cwd=tmp_path)


def test_a_file_whose_tick_time_is_the_midday_default_still_loads_with_the_midday_pass_off(tmp_path, caplog):
    path = tmp_path / "oracle.toml"
    path.write_text('[schedule]\ntime = "12:30"\n', encoding="utf-8")
    s = load_config(path, cwd=tmp_path)[0].schedule
    assert (s.time, s.midday_time) == ("12:30", "")
    assert "midday pass is off" in caplog.text
