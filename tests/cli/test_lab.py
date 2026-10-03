import json

from oracle.cli.main import main
from tests.cli.test_testbed import config
from tests.gui.test_lab_routes import TEMPLATE


def test_scoreboard_prints_every_test_with_the_bar_it_was_judged_by(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path)), "lab", "scoreboard"]) == 0
    out = capsys.readouterr().out
    assert "19 hypotheses registered" in out and "t >= 3.02" in out
    assert "momentum_12_1" in out and "t > 2.00" in out and "insider_buys" in out
    assert "trend_overlay: worst fall at most two thirds" in out


def test_register_show_and_drop_a_test(tmp_path, capsys):
    cfg = config(tmp_path)
    template = tmp_path / "t.json"
    template.write_text(json.dumps(TEMPLATE), encoding="utf-8")
    assert main(["--config", str(cfg), "lab", "register", str(template)]) == 0
    assert "Registered new_signal" in capsys.readouterr().out
    assert main(["--config", str(cfg), "lab", "show", "new_signal"]) == 0
    out = capsys.readouterr().out
    assert "bar: t >= 3.02" in out and "not committed" in out
    assert main(["--config", str(cfg), "lab", "drop", "new_signal"]) == 0
    template.write_text(json.dumps({**TEMPLATE, "colour": "red"}), encoding="utf-8")
    assert main(["--config", str(cfg), "lab", "register", str(template)]) != 0
    assert "not colour" in capsys.readouterr().err


def test_commit_refuses_a_bad_hash(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path)), "lab", "commit", "momentum_12_1", "zzz"]) != 0
    assert "not a commit hash" in capsys.readouterr().err


def test_costs_print_the_model_and_price_a_hand_trade(tmp_path, capsys):
    cfg = config(tmp_path)
    assert main(["--config", str(cfg), "lab", "costs", "--value", "10000", "--uk-shares"]) == 0
    out = capsys.readouterr().out
    assert "stamp duty 0.5% on UK share buys" in out and "Small Cap 0.50%" in out
    assert "A buy of £10,000.00: dealing £0.00, spread £10.00, stamp duty £50.00, FX £0.00; total £60.00" in out
    assert main(["--config", str(cfg), "lab", "costs", "--value", "10000", "--sell", "--currency", "usd"]) == 0
    assert "stamp duty £0.00" in capsys.readouterr().out


def test_strategies_strategy_and_trend_check_without_anything_kept(tmp_path, capsys):
    cfg = config(tmp_path)
    assert main(["--config", str(cfg), "lab", "strategies"]) == 0
    assert "No strategy test has code yet" in capsys.readouterr().out
    assert main(["--config", str(cfg), "lab", "strategy", "nope"]) != 0
    assert "no strategy code" in capsys.readouterr().err
    assert main(["--config", str(cfg), "lab", "strategy", "nope", "--show"]) == 1
    assert main(["--config", str(cfg), "lab", "trend-check"]) != 0
    assert "nothing to reproduce" in capsys.readouterr().err


def test_a_strategy_registration_shows_its_variants(tmp_path, capsys):
    cfg = config(tmp_path)
    template = tmp_path / "s.json"
    template.write_text(json.dumps({**TEMPLATE, "key": "s1", "kind": "strategy", "horizons": [], "variants": 3}),
                        encoding="utf-8")
    assert main(["--config", str(cfg), "lab", "register", str(template)]) == 0
    assert main(["--config", str(cfg), "lab", "show", "s1"]) == 0
    assert "variants tried: 3" in capsys.readouterr().out
