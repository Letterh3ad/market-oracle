from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services.context import open_context
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.sec import FakeSec
from tests.fakes.web import FakeWeb


@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('[sources.financedatabase]\nrate_per_second = 1000\n[research]\ncontact = "Faye faye@example.com"\n',
                   encoding="utf-8")
    sec = FakeSec()
    sec.companies["AAPL"] = (320193, "Apple Inc.")
    sec.filings[320193] = [("8-K", "2026-07-30", "0000320193-26-000090", "a.htm", "", "2.02,9.01",
                            "2026-07-30T16:31:00.000Z")]
    universe = [equity("AAPL", "Apple Inc.", currency="USD"), equity("BP.L", "BP", "United Kingdom", "LSE")]
    web = FakeWeb(FakeFinanceDatabase(universe), sec)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    _run("universe", "refresh")
    return _run


def test_events_lists_each_release_and_why_it_has_no_moves(run):
    code, out, _ = run("events", "aapl")
    assert code == 0
    assert "dates from SEC 8-K item 2.02" in out
    assert "2026-07-30  after open" in out and "no stored close near the date" in out


def test_events_says_when_there_is_no_source(run):
    code, out, _ = run("events", "BP.L")
    assert code == 0
    assert "no earnings dates source for BP.L" in out
