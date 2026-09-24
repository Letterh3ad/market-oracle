from datetime import timedelta

from holiday_watch.report import build_report, write_report
from holiday_watch.seams import DryRunSystem, Ran

from tests.holiday_watch.fakes import APP, NOW, FakeHttp, FakeSystem, healthy_routes, watch

STUDY = {
    "id": 1, "state": "running", "paused_reason": None, "total": 540, "done": 120, "pending": 400,
    "retrying": 2, "running": [{"id": 5}], "failed": [{"id": 9}, {"id": 10}],
    "top_failures": [["ollama timeout", 2]], "mean_replay_s": 410.6, "waiting": None,
    "polled_at": "2026-09-26T02:59:50Z",
    "score": {"excluded": 3, "prices_checked_at": "2026-09-25T22:10:00Z", "price_failures": ["X.L"],
              "horizons": {"1m": {"scored": 80, "pending": 40, "hit_rate": 0.55, "hit_rate_ci": [0.44, 0.65],
                                  "base_rate_up": 0.52, "expected_hit_rate": 0.51,
                                  "by_verdict": {"buy": {"n": 50, "mean_return": 0.012, "mean_as_called": 0.012}}}}},
}
REPORT = {"report": {"account": {"currency": "GBP", "capital": 1000000.0, "lines": [
    {"name": "ai", "caption": "The verdicts", "label": "", "return": 0.034, "max_fall": -0.05, "trades": 40,
     "costs": 1200.0},
    {"name": "all_buy", "caption": "Every verdict a buy", "label": "", "return": 0.021, "max_fall": -0.07,
     "trades": 90, "costs": 2500.0}]}}}


def routes():
    r = healthy_routes()
    r[f"{APP}/api/status"] = {**r[f"{APP}/api/status"], "server": {"pid": 321, "version": "abc1234"},
                              "tick": {"started_at": "2026-09-25T22:00:05Z", "outcome": "ok", "first_error": None},
                              "automation": {"enabled": True}}
    r[f"{APP}/api/replay"] = {"study": STUDY}
    r[f"{APP}/api/replay/report"] = REPORT
    return r


def seeded(tmp_path, **kw):
    w = watch(tmp_path, http=FakeHttp(routes()), **kw)
    w.cfg.fixruns.mkdir(parents=True)
    w.cfg.log_file.write_text(
        f"{(NOW - timedelta(hours=30)).isoformat()} app down: old news\n"
        f"{(NOW - timedelta(hours=2)).isoformat()} reaped claude PID 800 (idle for 31 min)\n", encoding="utf-8")
    (w.cfg.fixruns / "2026-09-26-0100-long_job.md").write_text("# Fix run: long_job\n\nCancelled job 7.\n",
                                                                encoding="utf-8")
    (w.cfg.fixruns / "2026-09-24-0100-app_down.md").write_text("# Fix run: app_down\n\nancient\n", encoding="utf-8")
    return w


def test_the_report_quotes_status_study_score_account_and_the_last_day(tmp_path):
    text = build_report(seeded(tmp_path))
    for piece in ("# Holiday report 2026-09-26", "App: up", "abc1234", "Last tick: 2026-09-25T22:00:05Z (ok)",
                  "120 of 540 done", "400 pending", "2 failed", "ollama timeout: 2", "mean replay 411 s",
                  "1m: scored 80, pending 40, hit rate 55.0% (44.0% to 65.0%)", "base rate up 52.0%",
                  "expected 51.0%", "buy: n 50, mean return 1.20%, as called 1.20%", "Excluded: 3",
                  "Price failures: X.L", "ai (The verdicts): +3.40%", "all_buy (Every verdict a buy): +2.10%",
                  "reaped claude PID 800", "Cancelled job 7."):
        assert piece in text, piece
    assert "old news" not in text and "ancient" not in text


def test_the_report_survives_the_app_being_down(tmp_path):
    w = watch(tmp_path, http=FakeHttp({}))
    text = build_report(w)
    assert "App: DOWN" in text and "No replay study" in text


def test_write_report_puts_claudes_summary_on_top(tmp_path):
    system = FakeSystem(result=Ran(0, "All running. Nothing to act on."))
    w = seeded(tmp_path, system=system)
    path = write_report(w)
    assert path == w.cfg.reports / "2026-09-26.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Holiday report 2026-09-26\n\n## Summary\n\nAll running.")
    (args, stdin), = system.ran
    assert "120 of 540 done" in stdin and args[args.index("--model") + 1] == "sonnet"
    assert "--tools" in args


def test_a_failed_summary_still_writes_the_plain_report(tmp_path):
    system = FakeSystem(result=Ran(1, "auth error"))
    w = seeded(tmp_path, system=system)
    text = write_report(w).read_text(encoding="utf-8")
    assert "Summary unavailable: claude exit 1" in text and "120 of 540 done" in text


def test_dry_run_prints_the_report_and_writes_nothing(tmp_path):
    real = FakeSystem()
    w = seeded(tmp_path, system=real)
    w.dry_run = True
    w.system = DryRunSystem(say=w.log, real=real)
    assert write_report(w) is None
    assert real.ran == [] and not (w.cfg.reports / "2026-09-26.md").exists()
    assert any("120 of 540 done" in line for line in w.lines)
