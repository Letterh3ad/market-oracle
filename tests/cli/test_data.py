from oracle.cli.main import main
from oracle.config import Config
from oracle.services.context import open_context
from tests.cli.test_testbed import config
from tests.services.test_data_check_service import clean, seed_live


def seed(tmp_path) -> None:
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None) as c:
        seed_live(c, [("VUSA.L", "Vanguard S&P 500 UCITS ETF", "etf", "vusa"), ("ABC-WT", "Abc", "equity", "abc")],
                  clean("VUSA.L") + clean("ABC-WT"))


def test_show_before_any_run_says_so(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path)), "data", "show"]) == 0
    assert "No data check has run yet" in capsys.readouterr().out


def test_check_runs_keeps_and_prints_counts_and_flagged_rows(tmp_path, capsys):
    seed(tmp_path)
    assert main(["--config", str(config(tmp_path)), "data", "check"]) == 0
    out = capsys.readouterr().out
    assert "Junk or fund ticker in a stock set" in out and "VUSA.L" in out and "listed as etf" in out
    assert "Split-sized jump with no recorded split" in out and "no research database" in out
    assert main(["--config", str(config(tmp_path)), "data", "show", "--check", "junk_ticker", "--limit", "1"]) == 0
    shown = capsys.readouterr().out
    assert "ABC-WT" in shown and "VUSA.L" not in shown and "1 more" in shown and "Split-sized" not in shown


def test_an_unknown_check_is_refused(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path)), "data", "show", "--check", "nope"]) == 1
    assert "no check nope" in capsys.readouterr().err
