from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services.context import open_context
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, summary

SEMIS = dict(currency="USD", sector="Information Technology", industry_group="Semiconductors")
NAMES = ["AMD", "NVDA", "INTC", "MU", "TXN", "QCOM"]


@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(
        "[sources.financedatabase]\nrate_per_second = 1000\n[sources.yahoo]\nrate_per_second = 1000\n"
        "[analysts]\npeers = 5\n",
        encoding="utf-8",
    )
    yahoo = FakeYahoo()
    for s in NAMES[1:4]:
        yahoo.summaries[s] = summary("USD", market_cap=1e11, trailingPE=25.0)
    web = FakeWeb(FakeFinanceDatabase([equity(s, f"{s} Corp", market_cap="Large Cap", **SEMIS) for s in NAMES]), yahoo)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    _run("universe", "refresh")
    return _run


def test_peers_lists_the_group_with_figures_and_missing_ones(run):
    code, out, _ = run("peers", "amd")
    assert code == 0
    assert "industry group Semiconductors" in out and "5 peers" in out
    assert "INTC" in out and "25.0" in out
    assert "no fundamentals" in out
