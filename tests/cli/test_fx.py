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
    yahoo.add("AAPL", "USD", "America/New_York", [bar("2026-09-17", 231.0), bar("2026-09-18", 232.0)])
    yahoo.add("GBPUSD=X", "USD", "Europe/London", [bar("2026-09-17", 1.35), bar("2026-09-18", 1.36)])
    web = FakeWeb(FakeFinanceDatabase([equity("AAPL", "Apple Inc.", currency="USD")]), yahoo)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    _run("universe", "refresh")
    _run("prices", "update", "AAPL")
    return _run


def test_fx_update_then_show(run):
    code, out, _ = run("fx", "update")
    assert code == 0 and "USD: 2 new rates from yahoo" in out
    code, out, _ = run("fx", "show", "usd", "--days", "1")
    assert out.splitlines()[1].split() == ["2026-09-18", "1.3600", "yahoo"]


def test_prices_show_in_base_currency(run):
    run("fx", "update")
    _, out, _ = run("prices", "show", "AAPL", "--base", "--days", "1")
    assert "GBP" in out.splitlines()[0]
    assert out.splitlines()[1].split()[-1] == f"{232.0 / 1.36:.2f}"


def test_a_bar_without_a_rate_shows_as_not_converted(run):
    _, out, _ = run("prices", "show", "AAPL", "--base", "--days", "1")
    assert out.splitlines()[1].split()[-1] == "n/a"
    assert "oracle fx update" in out


def test_a_currency_without_a_source_fails_the_update(run):
    # With every FX source failing, the command reports it and exits non-zero.
    code, out, _ = run("fx", "update", "--source", "open_er_api")
    assert code == 1 and "USD: no rates" in out


def test_open_er_api_rates_carry_its_attribution(tmp_path, capsys, monkeypatch):
    from tests.fakes.fx import FakeOpenEr, noon

    cfg = tmp_path / "oracle.toml"
    cfg.write_text("[sources.financedatabase]\nrate_per_second = 1000\n", encoding="utf-8")
    yahoo = FakeYahoo()
    yahoo.add("AAPL", "USD", "America/New_York", [bar("2026-09-18", 232.0)])
    open_er = FakeOpenEr(noon("2026-09-22"))
    open_er.rates = {"USD": 1.37}
    web = FakeWeb(FakeFinanceDatabase([equity("AAPL", "Apple Inc.", currency="USD")]), yahoo, open_er)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))
    for argv in (["universe", "refresh"], ["prices", "update", "AAPL"], ["fx", "update", "--source", "open_er_api"]):
        cli_main.main(["--config", str(cfg), *argv])
    capsys.readouterr()
    cli_main.main(["--config", str(cfg), "fx", "show", "USD"])
    assert "Rates By Exchange Rate API" in capsys.readouterr().out
