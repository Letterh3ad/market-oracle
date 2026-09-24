from datetime import datetime, timezone
from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services.context import open_context
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import BASIC, FakeFinanceDatabase


@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("[sources.financedatabase]\nrate_per_second = 1000\n", encoding="utf-8")
    clock = FakeClock(datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc))
    fdb = FakeFinanceDatabase(BASIC)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, clock=clock, transport=fdb.transport))

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    _run("universe", "refresh")
    return _run


def test_swap_moves_every_list_to_the_sibling(run):
    run("watch", "add", "AAPL")
    run("watch", "add", "AAPL", "--list", "tech")
    code, out, _ = run("watch", "swap", "AAPL", "APC.DE")
    assert code == 0 and out.strip() == "watching APC.DE instead of AAPL on main, tech"
    assert "APC.DE" in run("watch", "show", "tech")[1]


def test_swap_to_another_company_fails_with_the_reason(run):
    run("watch", "add", "AAPL")
    code, _, err = run("watch", "swap", "AAPL", "TM")
    assert code != 0 and "not a listing of Apple" in err
