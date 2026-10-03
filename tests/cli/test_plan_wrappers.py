import tomllib

from oracle.cli.main import main
from tests.cli.test_plan_money import config


def test_wrappers_with_nothing_entered_lists_what_is_missing_with_a_command_each(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path, "")), "plan", "wrappers"]) == 0
    out = capsys.readouterr().out
    assert "Where the next pound goes" in out and "Lifetime ISA: off" in out
    assert "oracle plan wrappers --salary" in out and "oracle plan wrappers --born" in out
    assert "oracle plan wrappers --needed-before" in out and "oracle plan set --monthly" in out
    assert "2026-27" in out and "gov.uk" in out


def test_wrappers_saves_numbers_and_the_lisa_switch_then_shows_the_order(tmp_path, capsys):
    cfg = config(tmp_path, "monthly = 600.0\n")
    args = ["--config", str(cfg), "plan", "wrappers", "--salary", "30000", "--employee-pct", "5", "--employer-pct",
            "3", "--born", "1996", "--needed-before", "200", "--lisa", "on"]
    assert main(args) == 0
    saved = tomllib.loads(cfg.read_text(encoding="utf-8"))["wrappers"]
    assert saved == {"salary": 30000.0, "employee_pct": 5.0, "employer_pct": 3.0, "born": 1996,
                     "needed_before": 200.0, "lisa": True}
    out = capsys.readouterr().out
    assert "Lifetime ISA: on" in out and "Saved [wrappers]" in out
    assert "1. Workplace pension first" in out and "2. Stocks and Shares ISA" in out and "Lifetime ISA:" in out
    assert "per £100 a month" in out.lower() or "Per £100 a month" in out
    assert main(["--config", str(cfg), "plan", "wrappers", "--lisa", "off"]) == 0
    assert tomllib.loads(cfg.read_text(encoding="utf-8"))["wrappers"]["lisa"] is False
    assert "Lifetime ISA: off" in capsys.readouterr().out


def test_wrappers_refuses_bad_values_cleanly(tmp_path, capsys):
    cfg = config(tmp_path, "")
    assert main(["--config", str(cfg), "plan", "wrappers", "--employee-pct", "150"]) != 0
    assert "employee_pct" in capsys.readouterr().err
    assert main(["--config", str(cfg), "plan", "wrappers", "--growth", "90"]) != 0
    assert "growth" in capsys.readouterr().err


def test_growth_errors_speak_the_flags_percent(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path, "")), "plan", "wrappers", "--growth", "90"]) != 0
    err = capsys.readouterr().err
    assert "--growth" in err and "%" in err and "0.05" not in err
