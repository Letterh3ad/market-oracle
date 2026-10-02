import asyncio
from datetime import date

from oracle.cli.main import main
from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.trend_service import SERIES, run_and_keep_trend
from tests.cli.test_testbed import config
from tests.fakes.clock import FakeClock
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo
from tests.services.test_trend_overlay_service import NOW, NY, bars, weekdays


async def kept(tmp_path):
    yahoo = FakeYahoo()
    yahoo.add(SERIES.symbol, "USD", NY, bars(weekdays(date(1988, 1, 4), date(2012, 12, 31))), kind="INDEX")
    cfg = Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db")
    with open_context(cfg, None, FakeClock(NOW), FakeWeb(yahoo).transport) as c:
        return await run_and_keep_trend(c)


def test_show_before_any_run_says_so(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path)), "plan", "trend", "--show"]) == 0
    assert "No trend overlay test kept yet" in capsys.readouterr().out


def test_trend_runs_keeps_and_prints_both_windows_crashes_and_the_verdict(tmp_path, capsys, monkeypatch):
    result = asyncio.run(kept(tmp_path))

    async def fake(ctx, say=None):
        return result

    monkeypatch.setattr("oracle.cli.plan.run_and_keep_trend", fake)
    assert main(["--config", str(config(tmp_path)), "plan", "trend"]) == 0
    out = capsys.readouterr().out
    assert "^SP500TR" in out and "10-month" in out and "Cash earns 0: no T-bill series" in out
    assert "choose   1988-01-04 2007-12-31" in out and "(reported only)" in out
    assert "confirm  2008-01-01 2012-12-31" in out
    assert "Trend overlay" in out and "Buy and hold" in out and "switches" in out and "in cash" in out
    assert "2008     2008-01-01 2009-12-31" in out
    assert f"Verdict (confirm): {result.verdict}" in out
    assert main(["--config", str(config(tmp_path)), "plan", "trend", "--show"]) == 0
    assert f"Verdict (confirm): {result.verdict}" in capsys.readouterr().out


async def kept_funds(tmp_path):
    from oracle.services.fund_compare_service import run_and_keep_funds
    from tests.services.test_fund_compare_service import NOW as FUNDS_NOW, market
    yahoo = FakeYahoo()
    market(yahoo)
    cfg = Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db")
    with open_context(cfg, None, FakeClock(FUNDS_NOW), FakeWeb(yahoo).transport) as c:
        return await run_and_keep_funds(c)


def test_funds_show_before_any_run_says_so(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path)), "plan", "funds", "--show"]) == 0
    assert "No fund comparison kept yet" in capsys.readouterr().out


def test_funds_prints_every_fund_as_a_proxy_and_every_tracking_overlap(tmp_path, capsys, monkeypatch):
    result = asyncio.run(kept_funds(tmp_path))

    async def fake(ctx, say=None):
        return result

    monkeypatch.setattr("oracle.cli.plan.run_and_keep_funds", fake)
    assert main(["--config", str(config(tmp_path)), "plan", "funds"]) == 0
    out = capsys.readouterr().out
    assert "GBP" in out and "2018-03-01 to 2020-12-31" in out
    for label in ("All-world", "Developed world", "S&P 500", "UK home-bias mix", "UK shares (part of the mix)"):
        assert label in out
    assert out.count("proxy") >= 5 and "TER drag" in out and "US share" in out and "0.19%" in out
    for real in ("VWRP.L", "SWDA.L", "VUAG.L", "VUKG.L"):
        assert real in out
    assert "Tracking" in out and "2019-09-02" in out
    assert "FX check" in out and "USD per GBP" in out
