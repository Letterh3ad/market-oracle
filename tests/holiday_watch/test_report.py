from datetime import timedelta

from holiday_watch.report import build_report, write_report
from holiday_watch.seams import Ran, dry

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
        f"{(NOW - timedelta(hours=2)).isoformat()} reaped claude PID 800 (idle for 31 min)\n"
        f"{(NOW - timedelta(hours=1)).isoformat()} [dry-run] would kill the process tree of PID 9\n",
        encoding="utf-8")
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
    assert "old news" not in text and "ancient" not in text and "[dry-run]" not in text



def band(label, ret, lines=("ai", "momentum")):
    return {"size": label.lower(), "label": label, "listings": ["A", "B"], "account": {
        "currency": "GBP", "capital": 1000000.0, "lines": [
            {"name": n, "caption": n, "label": "Momentum" if n == "momentum" else "", "return": ret, "max_fall": -0.1,
             "trades": 5, "costs": 10.0} for n in lines]}}


def test_the_report_shows_each_size_band_account_and_whatever_lines_it_returns(tmp_path):
    w = seeded(tmp_path)
    report = {**REPORT["report"], "by_size": [band("Large", 0.05), band("Mid and small", -0.02, ("ai", "later"))]}
    report["account"] = {**report["account"], "lines": [*report["account"]["lines"], band("x", 0.07)["account"]["lines"][1]]}
    w.http.routes[f"{APP}/api/replay/report"] = {"report": report}
    text = build_report(w)
    assert "momentum (Momentum): +7.00%" in text
    large, small = text.index("## Account, Large"), text.index("## Account, Mid and small")
    assert large < small < text.index("## Watchdog actions")
    assert "momentum (Momentum): +5.00%" in text[large:small] and "later (later): -2.00%" in text[small:]

def test_the_report_survives_the_app_being_down(tmp_path):
    w = watch(tmp_path, http=FakeHttp({}))
    text = build_report(w)
    assert "App: DOWN" in text and "No replay study" in text and "No ledgers" in text


def test_the_report_lists_every_paper_account_and_the_regime_mood(tmp_path):
    w = seeded(tmp_path)
    account = {"equity": 1_000_000.0, "return": 0.0, "max_fall": 0.0, "positions": [], "pending": []}
    w.http.routes[f"{APP}/api/ledgers"] = {"base_currency": "GBP", "accounts": [
        {**account, "name": "strategy"}, {**account, "name": "regime", "equity": 1_012_000.0, "return": 0.012,
                                          "max_fall": -0.004, "positions": [{"symbol": "KO"}]}]}
    w.http.routes[f"{APP}/api/ledgers/regime"] = {"day": "2026-09-25", "note": None,
                                                  "state": {"mood": "calm_up", "flags": ["clustered"]}}
    text = build_report(w)
    paper = text[text.index("## Paper accounts"):text.index("## Replay study")]
    assert "- strategy: equity 1,000,000 GBP, +0.00% since start" in paper
    assert "- regime: equity 1,012,000 GBP, +1.20% since start, largest fall -0.40%, 1 positions, 0 waiting" in paper
    assert "Regime mood on 2026-09-25: calm_up (clustered)" in paper
    w.http.routes[f"{APP}/api/ledgers/regime"] = {"day": None, "note": "the regime account is off", "state": None}
    assert "Regime: the regime account is off" in build_report(w)


def test_the_report_gives_the_regime_ai_tilt_or_why_there_was_none_and_any_lag(tmp_path):
    w = seeded(tmp_path)
    w.http.routes[f"{APP}/api/ledgers"] = {"base_currency": "GBP", "accounts": []}
    w.http.routes[f"{APP}/api/ledgers/regime"] = {"day": "2026-09-25", "note": None, "state": {"mood": "calm_up"},
                                                  "lag": "the step read 2026-09-18, 5 weekdays behind yesterday"}
    w.http.routes[f"{APP}/api/ledgers/regime-ai"] = {
        "day": "2026-09-25", "note": None, "state": {"mood": "calm_up"}, "lag": None,
        "tilt": {"day": "2026-09-25", "tilt": {"momentum": 0.6, "hold": 1.0}, "reason": "Rebound risk.", "error": None}}
    text = build_report(w)
    assert "Regime-AI tilt on 2026-09-25: momentum x0.60 (Rebound risk.)" in text
    assert "Regime WARNING: the step read 2026-09-18, 5 weekdays behind yesterday" in text
    w.http.routes[f"{APP}/api/ledgers/regime-ai"] = {
        "day": "2026-09-25", "note": None, "state": {}, "lag": None,
        "tilt": {"day": "2026-09-25", "tilt": {}, "reason": None, "error": "Ollama is not reachable"}}
    assert "Regime-AI: no tilt on 2026-09-25, rules weights traded (Ollama is not reachable)" in build_report(w)
    w.http.routes[f"{APP}/api/ledgers/regime-ai"] = {"day": None, "note": "the regime-ai account is off", "tilt": None}
    assert "Regime-AI: the regime-ai account is off" in build_report(w)


def test_the_report_gives_regime_v2s_mood_and_its_last_steps_rotations(tmp_path):
    w = seeded(tmp_path)
    w.http.routes[f"{APP}/api/ledgers"] = {"base_currency": "GBP", "accounts": []}
    w.http.routes[f"{APP}/api/ledgers/regime-v2"] = {
        "day": "2026-09-25", "note": None, "state": {"mood": "calm_up", "flags": []}, "lag": "behind",
        "decisions": [], "rotated_last_step": 2}
    text = build_report(w)
    assert "Regime-v2 mood on 2026-09-25: calm_up" in text
    assert "Regime-v2 rotated 2 holdings out on its last step (2026-09-25)" in text
    assert "Regime-v2 WARNING: behind" in text
    w.http.routes[f"{APP}/api/ledgers/regime-v2"] = {"day": None, "note": "the regime-v2 account is off", "state": None}
    assert "Regime-v2: the regime-v2 account is off" in build_report(w)


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
    dry(w)
    assert write_report(w) is None
    assert real.ran == [] and not (w.cfg.reports / "2026-09-26.md").exists()
    assert any("120 of 540 done" in line for line in w.lines)


def test_the_regime_line_is_listed_beside_momentum_in_every_account(tmp_path):
    w = seeded(tmp_path)
    regime = {"name": "regime", "caption": "Regime: ...", "label": "Regime", "return": -0.012, "max_fall": -0.08,
              "trades": 31, "costs": 900.0}
    size = band("Large", 0.05)
    size["account"]["lines"].append(regime)
    report = {**REPORT["report"], "by_size": [size]}
    report["account"] = {**report["account"], "lines": [*report["account"]["lines"], regime]}
    w.http.routes[f"{APP}/api/replay/report"] = {"report": report}
    text = build_report(w)
    said = "- regime (Regime): -1.20%, max fall -8.00%, 31 trades, costs 900"
    large = text.index("## Account, Large")
    assert said in text[:large] and said in text[large:]
