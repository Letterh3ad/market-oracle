import tomllib

from oracle.cli.main import main
from tests.cli.test_plan_money import config


def test_show_with_nothing_run_lists_what_is_missing_with_the_command_for_each(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path, "")), "plan", "show"]) == 0
    out = capsys.readouterr().out
    assert "Your plan" in out and "Fund" in out and "All-world" in out and "Stocks and Shares ISA" in out
    assert "Trend overlay" in out and "No (not tested yet)" in out
    assert "Missing:" in out and "oracle plan funds" in out and "oracle plan trend" in out
    assert "oracle plan set --monthly" in out


def test_set_saves_the_fund_and_monthly_and_keeps_the_platform(tmp_path, capsys):
    cfg = config(tmp_path)
    assert main(["--config", str(cfg), "plan", "set", "--fund", "sp500", "--monthly", "500"]) == 0
    plan = tomllib.loads(cfg.read_text(encoding="utf-8"))["plan"]
    assert plan == {"percent_fee": 0.25, "flat_fee": 4.99, "fund": "sp500", "monthly": 500.0}
    assert main(["--config", str(cfg), "plan", "show"]) == 0
    out = capsys.readouterr().out
    assert "S&P 500" in out and "Your pick" in out and "£500 a month" in out and "£23,952" in out


def test_set_takes_the_platform_too_and_refuses_bad_values_cleanly(tmp_path, capsys):
    cfg = config(tmp_path)
    assert main(["--config", str(cfg), "plan", "set", "--platform", "flat", "--dealing-fee", "1.5"]) == 0
    plan = tomllib.loads(cfg.read_text(encoding="utf-8"))["plan"]
    assert plan["platform"] == "flat" and plan["dealing_fee"] == 1.5 and plan["flat_fee"] == 4.99
    assert main(["--config", str(cfg), "plan", "set", "--fund", "gold"]) != 0
    assert "gold" in capsys.readouterr().err
    assert main(["--config", str(cfg), "plan", "set"]) != 0
    assert "nothing to set" in capsys.readouterr().err
