from pathlib import Path

import pytest

from holiday_watch.config import EXAMPLE, load


def test_defaults_without_a_file_hang_off_the_repo(tmp_path):
    cfg = load(tmp_path / "missing.toml")
    repo = Path(__file__).resolve().parents[2]
    assert cfg.repo == repo
    assert cfg.reports == repo / "holiday-reports"
    assert cfg.live.resolve() == (repo / ".." / "market-oracle-live").resolve()
    assert cfg.app_start[0] == str(repo / ".venv" / "Scripts" / "python.exe")
    assert cfg.app_start[-1] == "gui"
    assert cfg.fix_timeout_min == 30 and cfg.fix_max_per_day == 3 and cfg.fix_same_trigger_h == 4
    assert cfg.failure_rate == 0.2 and cfg.same_error == 3 and cfg.job_max_h == 2
    assert cfg.tick_grace_min == 45 and cfg.stall_h == 6


def test_file_values_override_and_placeholders_expand(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(
        '[paths]\nrepo = "D:/r"\nlive = "{repo}/../live"\nreports = "E:/logs"\n'
        '[app]\nurl = "http://127.0.0.1:9000"\n'
        '[remote_control]\nlaunch = ["claude", "--remote-control", "x"]\npid_file = "{reports}/rc.pid"\n'
        '[fix]\nmax_per_day = 1\n',
        encoding="utf-8")
    cfg = load(path)
    assert cfg.repo == Path("D:/r")
    assert cfg.live == Path("D:/r/../live")
    assert cfg.reports == Path("E:/logs")
    assert cfg.app_url == "http://127.0.0.1:9000"
    assert cfg.rc_launch == ("claude", "--remote-control", "x")
    assert cfg.rc_pid_file == Path("E:/logs/rc.pid")
    assert cfg.fix_max_per_day == 1
    assert cfg.python == Path("D:/r/.venv/Scripts/python.exe")


def test_unknown_keys_are_refused(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[fix]\nmax_per_dya = 1\n", encoding="utf-8")
    try:
        load(path)
    except ValueError as exc:
        assert "fix.max_per_dya" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_the_example_file_loads_and_names_every_setting():
    cfg = load(EXAMPLE)
    assert cfg.fix_timeout_min == 30
    text = EXAMPLE.read_text(encoding="utf-8")
    from holiday_watch.config import DEFAULTS
    for section, keys in DEFAULTS.items():
        assert f"[{section}]" in text
        for key in keys:
            assert f"{key} =" in text, f"{section}.{key} missing from the example"


def test_arm_settings_default_and_override(tmp_path):
    cfg = load(tmp_path / "missing.toml")
    assert cfg.arm_min_free_gb == 20 and cfg.arm_pass_every_min == 30 and cfg.arm_report_time == "07:00"
    assert cfg.arm_backups == cfg.repo / ".." / "oracle-backups"
    assert cfg.arm_expect_symbols == ()
    path = tmp_path / "c.toml"
    path.write_text('[paths]\nrepo = "D:/r"\n[arm]\nmin_free_gb = 5\nexpect_symbols = ["2330.TW"]\n'
                    'backups = "{repo}/bk"\n', encoding="utf-8")
    cfg = load(path)
    assert (cfg.arm_min_free_gb, cfg.arm_expect_symbols, cfg.arm_backups) == (5, ("2330.TW",), Path("D:/r/bk"))


def test_the_settings_file_is_the_env_one_else_beside_the_package(tmp_path, monkeypatch):
    from holiday_watch.config import DEFAULT_FILE, config_file
    monkeypatch.delenv("HOLIDAY_WATCH_CONFIG", raising=False)
    assert config_file() == DEFAULT_FILE
    monkeypatch.setenv("HOLIDAY_WATCH_CONFIG", str(tmp_path / "x.toml"))
    assert config_file() == tmp_path / "x.toml"


def test_arm_away_days_default_six(tmp_path):
    assert load(tmp_path / "missing.toml").arm_away_days == 6


@pytest.mark.parametrize("line", ["pass_every_min = 0", "pass_every_min = 4", "pass_every_min = 1441",
                                  'report_time = "7am"', 'report_time = "25:00"', "away_days = 0"])
def test_bad_arm_values_are_refused_at_load(tmp_path, line):
    path = tmp_path / "c.toml"
    path.write_text(f"[arm]\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="arm"):
        load(path)
