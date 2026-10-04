import asyncio

from oracle.cli.main import main
from oracle.config import Config
from oracle.services.context import open_context
from oracle.services.signal_test_service import keep_signal_test, run_signal_test
from tests.cli.test_testbed import config
from tests.fakes.clock import FakeClock
from tests.services.test_signal_tests import NOW, REGISTRY, seed


async def kept(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None, FakeClock(NOW)) as c:
        seed(c)
        return keep_signal_test(c, await run_signal_test(c, "future", registry=REGISTRY))


def test_list_shows_the_registry_before_any_run(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path)), "signals", "list"]) == 0
    out = capsys.readouterr().out
    assert "momentum_12_1        20 d  not run yet" in out and "reversal_1m           5 d  not run yet" in out
    assert "sue                  60 d  not run yet" in out and "earnings_yield       20 d  not run yet" in out


def test_test_prints_both_windows_power_missing_share_and_the_verdict(tmp_path, capsys, monkeypatch):
    result, seen = asyncio.run(kept(tmp_path)), {}

    async def fake(ctx, name, **kw):
        seen["name"] = name
        return result

    monkeypatch.setattr("oracle.cli.signals.run_and_keep", fake)
    assert main(["--config", str(config(tmp_path)), "signals", "test", "reversal_1m"]) == 0
    out = capsys.readouterr().out
    assert seen["name"] == "reversal_1m"
    assert "window   period" in out and "obs.power" in out and "missing" in out
    assert "train    2010-01-01 2019-12-31   5" in out and "(reported only)" in out
    assert "confirm  2020-01-01 2021-12-31   5" in out and "4+/4  PASS" in out
    assert "turnover  b/even" in out and "b/even: the one-way cost per trade" in out
    assert "2010-01-01 to 2010-06-30  IC        -  t      -     0 days  0 IC days (needs 20)" in out
    assert "Verdict (confirm): PASS" in out and "survivorship" in out


def test_train_only_is_passed_and_an_unseen_fail_says_what_was_needed(tmp_path, capsys, monkeypatch):
    import dataclasses
    result, seen = asyncio.run(kept(tmp_path)), {}
    w = dataclasses.replace(result.confirm[0], ic=0.004, t=0.5, observed_power=0.016, verdict="FAIL", passed=False)
    result = dataclasses.replace(result, confirm=(w,), verdict="FAIL", passed=False)

    async def fake(ctx, name, **kw):
        seen.update(kw)
        return result

    monkeypatch.setattr("oracle.cli.signals.run_and_keep", fake)
    assert main(["--config", str(config(tmp_path)), "signals", "test", "momentum_12_1", "--train-only"]) == 0
    out = capsys.readouterr().out
    assert seen["train_only"] is True
    assert "FAIL (could not see an IC this small: needed 0.0160)" in out
    assert "Verdict (confirm): FAIL (could not see an IC this small: needed 0.0160)" in out


def test_events_runs_the_study_and_show_prints_the_kept_one(tmp_path, capsys, monkeypatch):
    from oracle.analysts.event_study import EventRow
    from oracle.services.event_study_service import EventStudy, EventType
    from datetime import date
    rows = tuple(EventRow(h, 40, 30, 0.0123, 2.5, 25) for h in (1, 5, 20, 60))
    study = EventStudy((EventType("2.02", "results", rows, rows, "PASS", True),), 20, (1, 5, 20, 60),
                       date(2010, 1, 4), date(2026, 9, 30), date(2026, 6, 30), 0.2, 0.05, "2026-10-01T12:00:00+00:00")
    seen = {}

    async def fake(ctx, **kw):
        seen.update(kw)
        return study

    monkeypatch.setattr("oracle.cli.signals.run_and_keep_events", fake)
    assert main(["--config", str(config(tmp_path)), "signals", "events", "--train-only"]) == 0
    out = capsys.readouterr().out
    assert seen["train_only"] is True and "Verdict: PASS" in out and "+1.23%" in out and "   20      40     30" in out
    monkeypatch.setattr("oracle.cli.signals.event_studies", lambda ctx: {})
    assert main(["--config", str(config(tmp_path)), "signals", "events", "--show"]) == 0
    assert "No event study kept yet" in capsys.readouterr().out
