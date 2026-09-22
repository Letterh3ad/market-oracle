from datetime import date, datetime, timezone
from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services.context import open_context
from tests.fakes.clock import FakeClock
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
    yahoo.add("VOD.L", "GBp", "Europe/London", [bar("2026-09-17", 70.0)])
    web = FakeWeb(FakeFinanceDatabase([equity("VOD.L", "Vodafone", "United Kingdom", "LSE", currency="GBP")]), yahoo)
    clock = FakeClock(datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, clock=clock, transport=web.transport))

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    _run.yahoo, _run.clock = yahoo, clock
    _run("universe", "refresh")
    _run("watch", "add", "VOD.L")
    _run("prices", "update")
    return _run


def test_buy_then_prices_update_fills_it_and_show_lists_the_position(run):
    code, out, _ = run("paper", "buy", "vod.l", "1000")
    assert code == 0 and "VOD.L" in out and "next open" in out
    _, out, _ = run("paper", "show")
    assert "pending" in out and "manual" in out and "strategy" in out
    run.yahoo.series["VOD.L"].bars.append((date(2026, 9, 21), 71.0, 71.0, 71.0, 71.0, 71.0, 1000))
    run.clock.advance(3 * 86400)
    _, out, _ = run("prices", "update")
    assert "VOD.L: bought" in out
    _, out, _ = run("paper", "show")
    assert "VOD.L" in out and "pending" not in out


def test_sell_all_queues_and_shows_as_pending(run):
    run("paper", "buy", "VOD.L", "1000")
    run.yahoo.series["VOD.L"].bars.append((date(2026, 9, 21), 71.0, 71.0, 71.0, 71.0, 71.0, 1000))
    run.clock.advance(3 * 86400)
    run("prices", "update")
    code, out, _ = run("paper", "sell", "VOD.L", "--all")
    assert code == 0 and "whole position" in out
    _, out, _ = run("paper", "show", "--account", "manual")
    assert "pending sell VOD.L, the whole position" in out and "realised +0.00" in out


def test_selling_what_is_not_held_is_an_error(run):
    code, _, err = run("paper", "sell", "VOD.L", "--shares", "1")
    assert code == 1 and "not held" in err


def test_show_scores_both_accounts_and_history_lists_equity(run):
    _, out, _ = run("paper", "show")
    assert "strategy: equity 10,000.00" in out and "+0.00% since start" in out and "costs paid" in out
    run("paper", "fill")
    _, out, _ = run("paper", "history", "--account", "manual")
    assert "2026-09-18" in out and "10,000.00" in out
