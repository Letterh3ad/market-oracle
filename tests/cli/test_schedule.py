from oracle.cli import main as cli_main
from oracle.config import load_config


def test_schedule_shows_off_then_turns_on_at_a_time_and_saves_it(tmp_path, capsys):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"\n', encoding="utf-8")
    assert cli_main.main(["--config", str(cfg), "schedule"]) == 0
    assert capsys.readouterr().out.startswith("off (would run weekdays at 22:30)")
    assert cli_main.main(["--config", str(cfg), "schedule", "--on", "--time", "06:15"]) == 0
    assert "on, weekdays at 06:15; next run" in capsys.readouterr().out
    schedule = load_config(cfg)[0].schedule
    assert (schedule.enabled, schedule.time) == (True, "06:15")


def test_a_bad_time_is_an_error(tmp_path, capsys):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"\n', encoding="utf-8")
    assert cli_main.main(["--config", str(cfg), "schedule", "--time", "6pm"]) == 1
    assert "22:30" in capsys.readouterr().err
