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


def test_fundamentals_passes_a_smoke_runs_bounds_and_status_prints_the_years(tmp_path, capsys, monkeypatch):
    from oracle.services.xbrl_service import XbrlCoverage, XbrlRun, XbrlYear
    seen = {}

    async def fake(ctx, **kw):
        seen.update(kw)
        return XbrlRun(members=1, with_facts=1)

    monkeypatch.setattr("oracle.cli.testbed.fetch_fundamentals", fake)
    assert main(["--config", str(config(tmp_path)), "testbed", "fundamentals", "--only", "AAA", "--limit", "1"]) == 0
    assert (seen["only"], seen["limit"], seen["refresh"]) == (["AAA"], 1, False)
    assert "1 members" in capsys.readouterr().out
    names = ("sue", "gross_profitability", "earnings_yield")
    cover = XbrlCoverage((XbrlYear(2012, 500, 480, 470, dict(zip(names, (300, 400, 450))),
                                   dict(zip(names, (900, 1500, 1700)))),), 600, 560, 550, ("OLD",), ("BANK",), None,
                         "2026-10-01T10:00:00+00:00")
    monkeypatch.setattr("oracle.cli.testbed.fundamentals_coverage", lambda ctx: cover)
    assert main(["--config", str(config(tmp_path)), "testbed", "fundamentals-status"]) == 0
    out = capsys.readouterr().out
    assert "600 priced members, 560 with a CIK, 550 with facts" in out and "Last fetch: none yet" in out
    assert "2012       500   480    470" in out and "300 members, 900 filings" in out
    assert "No CIK in SEC's ticker map (1): OLD" in out and "No XBRL facts at SEC (1): BANK" in out


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


def test_insiders_passes_a_smoke_runs_bounds_and_status_prints_the_years(tmp_path, capsys, monkeypatch):
    from oracle.services.insider_service import InsiderCoverage, InsiderRun
    seen = {}

    async def fake(ctx, **kw):
        seen.update(kw)
        return InsiderRun(members=1, issuers=1, quarters_fetched=2)

    monkeypatch.setattr("oracle.cli.testbed.fetch_insiders", fake)
    assert main(["--config", str(config(tmp_path)), "testbed", "insiders", "--only", "AAA", "--refresh"]) == 0
    assert (seen["only"], seen["limit"], seen["refresh"]) == (["AAA"], None, True)
    assert "2 quarters of trades" in capsys.readouterr().out
    from oracle.services.insider_service import LateCik
    from datetime import date
    cover = InsiderCoverage(600, 590, 10, 590, {"2009q1": 5, "2026q2": 7},
                            {2015: {"P": 812, "P_issuers": 140, "S": 9100, "4": 30000, "8-K 2.02": 2300}}, None,
                            (LateCik("XOM", 2115436, date(2010, 1, 4), None),))
    monkeypatch.setattr("oracle.cli.testbed.insider_coverage", lambda ctx: cover)
    assert main(["--config", str(config(tmp_path)), "testbed", "insiders-status"]) == 0
    out = capsys.readouterr().out
    assert "590 issuers with a CIK (10 members without)" in out and "2 quarters, 2009q1 to 2026q2" in out
    assert "2015        812      140     9100    30000        0     2300        0        0" in out
    assert "Today's CIK starts late (1)" in out and "XOM      CIK 2115436  member from 2010-01-04" in out
