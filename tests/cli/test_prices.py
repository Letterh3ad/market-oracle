from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services.context import open_context
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
    yahoo.add("AAPL", "USD", "America/New_York", [bar("2026-09-17", 231.0), bar("2026-09-18", 232.5)])
    web = FakeWeb(FakeFinanceDatabase([equity("AAPL", "Apple Inc.")]), yahoo)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    _run("universe", "refresh")
    return _run


def test_update_then_show(run):
    code, out, _ = run("prices", "update", "aapl")
    assert code == 0
    assert "AAPL: 2 new bars" in out and "2026-09-18" in out and "yahoo" in out
    code, out, _ = run("prices", "show", "AAPL", "--days", "1")
    assert code == 0
    rows = out.splitlines()[1:]
    assert len(rows) == 1 and rows[0].split()[:2] == ["2026-09-18", "232.50"]
    assert "USD" in out


def test_show_before_any_update_says_how(run):
    code, out, _ = run("prices", "show", "AAPL")
    assert code == 0 and "oracle prices update AAPL" in out


def test_an_unavailable_price_is_an_error_without_a_traceback(run):
    code, _, err = run("prices", "update", "NOPE")
    assert code == 1 and "no listing" in err and "Traceback" not in err


def test_sources_lists_the_chain_and_why_a_source_is_off(run, monkeypatch):
    monkeypatch.delenv("ORACLE_EODHD_KEY", raising=False)
    code, out, _ = run("prices", "sources")
    assert code == 0
    rows = [line.split(None, 2) for line in out.splitlines()[1:]]
    assert rows == [["1", "yahoo", "on"], ["2", "eodhd", "off (no key)"]]


def test_a_dead_listing_is_skipped_not_failed_and_says_how_to_check_again(run, monkeypatch):
    from datetime import date

    from oracle.services import prices_service
    from oracle.store.health import HealthRepo

    real = prices_service.WatchlistRepo

    def dead_first(conn):
        HealthRepo(conn).note_no_data("AAPL", date(2026, 9, 1), dead_after=1)
        return real(conn)

    run("watch", "add", "AAPL")
    monkeypatch.setattr(prices_service, "WatchlistRepo", dead_first)
    code, out, _ = run("prices", "update")
    assert code == 0
    assert "AAPL: skipped: no source has data since 2026-09-01 (check again with `oracle prices update AAPL`)" in out
