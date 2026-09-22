from datetime import date
from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services.context import open_context
from tests.analysts.bars import geometric, weekdays
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar


@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(
        "[sources.financedatabase]\nrate_per_second = 1000\n[sources.yahoo]\nrate_per_second = 1000\n",
        encoding="utf-8",
    )
    yahoo = FakeYahoo()
    days = weekdays(300, date(2025, 7, 1))
    yahoo.add("AAPL", "USD", "America/New_York", [bar(d.isoformat(), c) for d, c in zip(days, geometric(300, -0.001))])
    yahoo.add("NEW", "USD", "America/New_York", [bar(d.isoformat(), 10.0) for d in days[-30:]])
    universe = [equity("AAPL", "Apple Inc.", currency="USD"), equity("NEW", "New Co", currency="USD")]
    web = FakeWeb(FakeFinanceDatabase(universe), yahoo)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    for argv in (["universe", "refresh"], ["prices", "update", "AAPL"], ["prices", "update", "NEW"]):
        _run(*argv)
    return _run


def test_analyse_prints_each_signal_with_its_reason(run):
    code, out, _ = run("analyse", "aapl")
    assert code == 0
    assert "AAPL" in out and "trend" in out and "bearish" in out
    assert "below its 200-day average" in out


def test_a_skipped_analyst_says_why(run):
    code, out, _ = run("analyse", "NEW")
    assert code == 0
    assert "trend" in out and "no signal" in out and "a year of prices" in out


def test_an_unknown_symbol_is_an_error(run):
    code, _, err = run("analyse", "MSFT")
    assert code == 1 and "no listing" in err
