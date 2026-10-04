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
    out = capsys.readouterr().out
    assert "Registered new_signal" in out and "near-FAIL check ran on keywords only" in out
    assert main(["--config", str(cfg), "lab", "show", "new_signal"]) == 0
    out = capsys.readouterr().out
    assert "bar: t >= 3.02" in out and "not committed" in out
    assert "near-FAIL check: ran on keywords only" in out
    assert main(["--config", str(cfg), "lab", "drop", "new_signal"]) == 0
    template.write_text(json.dumps({**TEMPLATE, "colour": "red"}), encoding="utf-8")
    assert main(["--config", str(cfg), "lab", "register", str(template)]) != 0
    assert "not colour" in capsys.readouterr().err


def test_commit_refuses_a_bad_hash(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path)), "lab", "commit", "momentum_12_1", "zzz"]) != 0
    assert "not a commit hash" in capsys.readouterr().err
