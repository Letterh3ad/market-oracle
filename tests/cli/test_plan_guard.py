import tomllib

from oracle.cli.main import main
from oracle.services import guardrails_service
from tests.cli.test_plan_money import config
from tests.services.test_drawdown_preview import SERIES


def test_a_rule_is_written_at_once_and_a_change_waits_until_cancelled(tmp_path, capsys):
    cfg = config(tmp_path)
    assert main(["--config", str(cfg), "plan", "rule", "--set", "Never sell in a fall."]) == 0
    assert tomllib.loads(cfg.read_text(encoding="utf-8"))["plan"]["rule"] == "Never sell in a fall."
    assert main(["--config", str(cfg), "plan", "rule", "--clear"]) == 0
    out = capsys.readouterr().out
    assert "Pending until" in out and "No-sell rule: Never sell in a fall." in out
    assert main(["--config", str(cfg), "plan", "pending"]) == 0
    assert "rule 'Never sell in a fall.' -> ''" in capsys.readouterr().out
    assert main(["--config", str(cfg), "plan", "pending", "--cancel"]) == 0
    out = capsys.readouterr().out
    assert "Cancelled" in out and "Nothing pending." in out and "cancelled" in out
    assert main(["--config", str(cfg), "plan", "pending", "--cancel"]) != 0
    assert "nothing pending" in capsys.readouterr().err


def test_drawdowns_print_each_crash_for_the_pot(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(guardrails_service, "fund_series", lambda ctx, key, monthly=False: SERIES[key])
    cfg = config(tmp_path, "monthly = 500.0\n")
    assert main(["--config", str(cfg), "plan", "drawdowns", "--pot", "20000"]) == 0
    out = capsys.readouterr().out
    assert "2008-09" in out and "S&P 500 *" in out and "-25.0%" in out and "15,000" in out
    assert "paying in:" in out and "stands in" in out
