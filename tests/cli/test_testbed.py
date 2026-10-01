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
    assert "Membership: fja05680/sp500 at abc1234" in out and "missing share 66.7%, 33.3% with renames mapped" in out
    assert "2010         3           3          1    66.7%    33.3%        1" in out
    assert "Renames: 1 accepted, 1 kept for review" in out
    assert "1 members priced on under 50% of their member-days:" in out
    assert "  BBB      2010-01-04 to 2010-01-04, 1 member-days: not fetched yet" in out
    assert "Its last change is 2010-01-04: to " in out


def test_aliases_lists_the_accepted_then_those_kept_for_review_and_verify_runs_the_check(tmp_path, capsys,
                                                                                          monkeypatch):
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None) as c:
        seed(c)
    assert main(["--config", str(config(tmp_path)), "testbed", "aliases"]) == 0
    out = capsys.readouterr().out
    assert "1 accepted, 1 kept for review (never applied)" in out
    assert out.index("FB     -> META") < out.index("EA     -> FERG")
    assert "FB     -> META   2010-01-05 accepted" in out
    seen = {}

    async def fake(ctx, **kw):
        seen.update(kw)
        return TestbedRun(renames="1 renames accepted")

    monkeypatch.setattr("oracle.cli.testbed.build_testbed", fake)
    assert main(["--config", str(config(tmp_path)), "testbed", "aliases", "--verify"]) == 0
    assert seen["verify"] is True and "1 renames accepted" in capsys.readouterr().out


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
