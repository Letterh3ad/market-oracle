from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services import tick_service
from oracle.services.context import open_context
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar


@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(
        "[sources.financedatabase]\nrate_per_second = 1000\n[sources.yahoo]\nrate_per_second = 1000\n"
        "[discovery]\nenabled = false\n",
        encoding="utf-8",
    )
    yahoo = FakeYahoo()
    yahoo.add("AAPL", "USD", "America/New_York", [bar("2026-09-17", 231.0), bar("2026-09-18", 232.5)])
    yahoo.add("GBPUSD=X", "USD", "Europe/London", [bar("2026-09-17", 1.25), bar("2026-09-18", 1.25)])
    web = FakeWeb(FakeFinanceDatabase([equity("AAPL", "Apple Inc."), equity("NOBARS", "No Bars")]), yahoo)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    _run("universe", "refresh")
    _run("watch", "add", "AAPL")
    return _run


def test_a_tick_prints_each_stage_and_is_read_back_by_last(run):
    assert run("tick", "--last")[1].startswith("no tick has run yet")
    code, out, _ = run("tick")
    assert code == 0 and "(manual): ok" in out
    assert [line.split()[0] for line in out.splitlines()[1:]] == ["prices", "fx", "fill", "analyse", "discover", "research", "step", "ai_step"]
    assert run("tick", "--last")[1] == out


def test_a_partial_tick_still_exits_zero_and_shows_the_error(run):
    run("watch", "add", "NOBARS")
    code, out, _ = run("tick")
    assert code == 0 and ": partial" in out and "NOBARS" in out


def test_exit_code_is_non_zero_only_when_every_stage_failed(run, monkeypatch):
    async def boom(ctx, say):
        raise RuntimeError("broken")

    monkeypatch.setattr(tick_service, "STAGES", {name: boom for name in tick_service.STAGES})
    code, out, _ = run("tick")
    assert code == 1 and ": failed" in out and "broken" in out


def test_tick_fill_runs_a_fill_pass_only(run):
    code, out, _ = run("tick", "--fill")
    assert code == 0 and out.startswith("fill pass ") and "(manual): ok" in out
    assert [line.split()[0] for line in out.splitlines()[1:]] == ["prices", "fx", "fill"]
    assert run("tick", "--last")[1].startswith("no tick has run yet")
