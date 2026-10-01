from datetime import date

from oracle.cli.main import main
from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.testbed_service import TestbedRun
from tests.gui.test_testbed_routes import seed


def config(tmp_path):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('db_path = "o.db"\nresearch_db_path = "r.db"\n', encoding="utf-8")
    return cfg


def test_status_prints_the_missing_share_per_year_and_every_unpriced_member(tmp_path, capsys):
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None) as c:
        seed(c)
    assert main(["--config", str(config(tmp_path)), "testbed", "status"]) == 0
    out = capsys.readouterr().out
    assert f"Signal testbed in {tmp_path / 'r.db'}" in out
    assert "Membership: fja05680/sp500 at abc1234" in out and "missing share 50.0%" in out
    assert "2010         2           2          1    50.0%        1" in out
    assert "  BBB      2010-01-04 to 2010-01-04, 1 member-days: not fetched yet" in out


def test_build_passes_a_smoke_runs_bounds_and_fails_on_a_stop(tmp_path, capsys, monkeypatch):
    seen = {}

    async def fake(ctx, **kw):
        seen.update(kw, research=ctx.config.research_db_path)
        return TestbedRun(members=2, stopped="5 requests failed in a row; run it again to resume")

    monkeypatch.setattr("oracle.cli.testbed.build_testbed", fake)
    code = main(["--config", str(config(tmp_path)), "testbed", "build", "--limit", "2", "--only", "AAA", "BRK.B",
                 "--from", "2010-01-01", "--to", "2011-12-31"])
    out = capsys.readouterr().out
    assert code == 1 and "run it again to resume" in out
    assert (seen["limit"], seen["only"], seen["start"], seen["end"], seen["refresh"]) == (
        2, ["AAA", "BRK.B"], date(2010, 1, 1), date(2011, 12, 31), False)
    assert seen["research"] == tmp_path / "r.db"
