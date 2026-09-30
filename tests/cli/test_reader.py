from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.cli import reader as reader_cli
from oracle.services import reader_service
from oracle.services.context import open_context
from oracle.services.reader_service import PairGroup, ReaderRun
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo


@pytest.fixture
def cli(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("[sources.financedatabase]\nrate_per_second = 1000\n", encoding="utf-8")
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), FakeYahoo())
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))
    monkeypatch.setattr(reader_service, "reader_groups", lambda c, w: [PairGroup("AAPL", "AAPL", "own", ())])

    def run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    run("universe", "refresh")
    return run


def test_plan_counts_and_estimates(cli):
    code, out, _ = cli("reader", "plan")
    assert code == 0 and "0 of 0 headlines to read" in out and "the bake-off's rate" in out


def test_run_yields_to_live_work_in_any_process_and_exits_1_when_stopped(cli, monkeypatch):
    seen = []

    def fake(ctx, watchlist, **kw):
        seen.append((watchlist, kw["busy"]))
        return ReaderRun("ollama:fake", stopped="cancelled")

    monkeypatch.setattr(reader_cli, "score_reader", fake)
    monkeypatch.setattr(reader_cli, "waits_anywhere", lambda ctx: "research AAPL")
    code, out, _ = cli("reader", "run")
    assert code == 1 and "stopped early (cancelled)" in out
    [(watchlist, busy)] = seen
    assert watchlist == "main" and busy() == "research AAPL"
