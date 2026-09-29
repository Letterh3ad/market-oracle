import pytest

from oracle.cli.main import main
from oracle.config import Config
from oracle.services.context import open_context
from tests.fakes.clock import FakeClock
from tests.services.test_backtest import BARS, END, LONG, NOW, START, WF_END, WF_START, rising, seed


def test_backtest_prints_every_line_the_notes_and_the_trades(tmp_path, capsys):
    with open_context(Config(db_path=tmp_path / "o.db", base_currency="USD"), None, FakeClock(NOW)) as c:
        seed(c, {"UP": BARS})
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('db_path = "o.db"\nbase_currency = "USD"\n', encoding="utf-8")
    code = main(["--config", str(cfg), "backtest", "--list", "tech", "--from", START.isoformat(),
                 "--to", END.isoformat(), "--slippage-bps", "5", "--commission", "1"])
    out = capsys.readouterr().out
    assert code == 0
    assert f"Backtest of the tech list (1 listing), {START.isoformat()} to {END.isoformat()}" in out
    assert "survivorship bias" in out and "5 basis points" in out and "1.00 USD commission" in out
    assert "strategy" in out and "buy_and_hold" in out and "momentum" in out
    assert "\nregime " in out and "Regime: trend, 12-1 momentum" in out and "no AI verdicts" in out
    assert " buy  UP " in out


def test_a_bad_day_is_refused_before_anything_runs(tmp_path, capsys):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('db_path = "o.db"\n', encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--config", str(cfg), "backtest", "--from", "yesterday", "--to", "2026-09-01"])
    assert exc.value.code == 2
    assert "not a date like" in capsys.readouterr().err


def test_walk_forward_prints_each_lines_segments_the_wins_and_regime_wf_fits(tmp_path, capsys):
    with open_context(Config(db_path=tmp_path / "o.db", base_currency="USD"), None, FakeClock(NOW)) as c:
        seed(c, {"UP": LONG, "SLOW": rising(700, "SLOW", daily=0.001)})
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('db_path = "o.db"\nbase_currency = "USD"\n', encoding="utf-8")
    code = main(["--config", str(cfg), "backtest", "--list", "tech", "--from", WF_START.isoformat(),
                 "--to", WF_END.isoformat(), "--walk-forward"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Walk-forward: 3 back-to-back segments of 6 months" in out and "fresh start" in out
    assert "\nregime_wf " in out and "Regime-wf: the regime book with its mood weights refitted" in out
    table = out[out.index("Walk-forward by segment"):]
    assert WF_START.isoformat() in table and "beat buy and hold" in table and "Sharpe" in table
    assert "regime_wf" in table and "all segments" in table
    assert "Regime-wf weights fitted" in out and "calm_up" in out
