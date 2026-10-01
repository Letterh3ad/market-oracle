import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from oracle.analysts.predictive import mean, own_return, spearman
from oracle.broker.regime import PRESETS, RegimeSettings
from oracle.services.laya_history_service import score_history
from oracle.services.laya_line import (
    METHOD, LayaInput, LayaVerdict, daily_scores, laya_input, laya_live, laya_only, laya_verdict, segment_start, verdict_note,
    with_laya,
)
from oracle.services.laya_test_service import FIT_DAYS, TEST_START, build_panel, keep_laya_test, run_laya_test
from oracle.services.backtest_service import run_backtest
from oracle.store.industry_news import IndustryNewsRepo
from tests.fakes.laya import FakeLaya
from tests.services.test_backtest import BARS, END as BT_END, START as BT_START, make_ctx, market as stored  # noqa: F401
from tests.broker.test_backtest import rising
from oracle.services.watch_service import DEFAULT_LIST
from tests.services.test_laya_test import END, MAINS, MODEL, backfill, ctx, market  # noqa: F401 (fixture)

FIRST = TEST_START - timedelta(days=FIT_DAYS)
SECOND = date(2023, 3, 21)
"""The second segment's start: the first with a trailing fit in this market."""


def panel_of(ctx, end=END):
    return build_panel(ctx, model=MODEL, start=FIRST, end=end, research=True)


def test_segments_are_the_tests_six_month_ones():
    assert segment_start(TEST_START - timedelta(days=1)) is None
    assert segment_start(TEST_START) == TEST_START and segment_start(SECOND - timedelta(days=1)) == TEST_START
    assert segment_start(SECOND) == SECOND and segment_start(date(2026, 10, 1)) == date(2026, 9, 21)


def test_scores_start_with_the_first_fitted_segment_dated_the_day_before_and_stay_in_range(ctx):
    market(ctx, signal=True)
    score_history(ctx, scorer=FakeLaya())
    scores = daily_scores(panel_of(ctx), TEST_START, END)
    assert set(scores) == {s for s, _ in MAINS}
    days = {d for rows in scores.values() for d, _ in rows}
    assert min(days) == TEST_START - timedelta(days=1), "August's news fits the first segment here"
    assert max(days) == END - timedelta(days=1)
    as_06 = build_panel(ctx, model=MODEL, end=END, research=True)
    late = {d for rows in daily_scores(as_06, TEST_START, END).values() for d, _ in rows}
    assert min(late) == SECOND - timedelta(days=1), "06's panel has no news before the test: no fit, no score"
    assert all(-1 <= v <= 1 for rows in scores.values() for _, v in rows)
    assert all(rows == sorted(rows) for rows in scores.values())


def test_news_that_leads_the_price_ranks_the_next_returns(ctx):
    market(ctx, signal=True)
    score_history(ctx, scorer=FakeLaya())
    panel = panel_of(ctx)
    scores = {m: dict(rows) for m, rows in daily_scores(panel, SECOND, END).items()}
    ics = []
    for t in sorted({d + timedelta(days=1) for rows in scores.values() for d in rows}):
        xs, ys = [], []
        for m, by_day in scores.items():
            bars = panel.bars[m]
            r = own_return(bars, [b.date for b in bars], t, 5)
            if r is not None and (t - timedelta(days=1)) in by_day:
                xs.append(by_day[t - timedelta(days=1)])
                ys.append(r[0])
        if (ic := spearman(xs, ys)) is not None:
            ics.append(ic)
    assert len(ics) > 100 and mean(ics) > 0.05


def test_no_score_reads_a_day_after_it(ctx):
    market(ctx, signal=True)
    score_history(ctx, scorer=FakeLaya())
    full = panel_of(ctx)
    cut = date(2023, 11, 15)
    short = replace(full, bars={s: [b for b in bars if b.date <= cut] for s, bars in full.bars.items()},
                    daily={s: {d: v for d, v in days.items() if d <= cut} for s, days in full.daily.items()})
    before = {m: [x for x in rows if x[0] < cut] for m, rows in daily_scores(full, TEST_START, END).items()}
    assert daily_scores(short, TEST_START, cut) == daily_scores(full, TEST_START, cut) == before
    assert any(rows for rows in before.values())


def test_the_live_score_reads_what_the_daily_research_found_too(ctx):
    market(ctx, signal=True)
    score_history(ctx, scorer=FakeLaya())
    assert build_panel(ctx, model=MODEL, start=FIRST, end=END).daily == panel_of(ctx).daily
    [(moved, day)] = ctx.conn.execute(
        "SELECT h.article_id, substr(a.published_at, 1, 10) FROM history_articles h JOIN articles a "
        "ON a.id = h.article_id WHERE h.symbol = 'HMC' AND a.published_at < '2024-03-01' ORDER BY a.published_at DESC LIMIT 1").fetchall()
    ctx.conn.execute("DELETE FROM history_articles WHERE article_id = ?", (moved,))
    IndustryNewsRepo(ctx.conn).record_fetch("HMC", [moved], 1, "2026-09-30T00:00:00+00:00")
    backfill_only, both = build_panel(ctx, model=MODEL, start=FIRST, end=END).daily, panel_of(ctx).daily
    assert backfill_only["HMC"] != both["HMC"], f"the research article of {day} counts only with the research"
    assert backfill_only["GM"] != both["GM"], "and as a competitor article for its rivals"
    got = laya_input(ctx, ["HMC", "GM", "NOPE"], TEST_START, END, model=MODEL)
    assert set(got.scores) == {"HMC", "GM"} and got.verdict.verdict is None


def test_without_the_list_or_any_listing_there_is_no_score_and_no_error(ctx):
    assert laya_input(ctx, ["HMC"], TEST_START, END, watchlist="nope", model=MODEL).scores == {}
    assert laya_input(ctx, [], TEST_START, END, model=MODEL).scores == {}


def kept(ctx, verdict, beats=True, run="main", model=MODEL, **more):
    t = run_laya_test(ctx, model=MODEL, end=END)
    horizons = tuple(replace(h, beats_baseline=beats, beats_momentum=beats) for h in t.horizons)
    return keep_laya_test(ctx, replace(t, run=run, model=model, verdict=verdict, passed=verdict == "PASS",
                                       horizons=horizons, **more))


def test_only_the_newest_main_verdict_for_the_model_that_passed_and_beat_both_baselines_gives_weight(ctx):
    market(ctx, signal=True)
    score_history(ctx, scorer=FakeLaya())
    assert laya_verdict(ctx, MODEL) == LayaVerdict(None, False, MODEL, why="no predictive test kept for this model: "
                                                                             "weight 0")
    kept(ctx, "PASS", run="sensitivity")
    kept(ctx, "PASS", model="ollama:other")
    assert not laya_verdict(ctx, MODEL).useful, "neither the sensitivity run nor another model counts"
    kept(ctx, "PASS", beats=False)
    v = laya_verdict(ctx, MODEL)
    assert v.verdict == "PASS" and not v.useful and "did not beat both baselines" in v.why
    newest = kept(ctx, "PASS")
    v = laya_verdict(ctx, MODEL)
    assert v.useful and v.test_id == newest.id
    kept(ctx, "FAIL")
    assert laya_verdict(ctx, MODEL).verdict == "FAIL" and not laya_verdict(ctx, MODEL).useful


def test_a_result_kept_before_the_cutoff_rule_gives_no_weight_and_lays_fail_stands(ctx):
    from oracle.research.laya import MODEL_TAG

    market(ctx, signal=True)
    score_history(ctx, scorer=FakeLaya())
    kept(ctx, "PASS", cutoff=None)
    v = laya_verdict(ctx, MODEL)
    assert v.verdict == "PASS" and not v.useful and "before the training-cutoff rule" in v.why
    kept(ctx, "PASS", model=MODEL_TAG)
    v = laya_verdict(ctx, MODEL_TAG)
    assert v.verdict == "FAIL" and not v.useful and "FAIL stands" in v.why


def test_only_a_run_on_the_main_list_from_the_tests_start_gives_weight(ctx):
    market(ctx, signal=True)
    score_history(ctx, scorer=FakeLaya())
    assert run_laya_test(ctx, model=MODEL, end=END).watchlist == DEFAULT_LIST, "each result names its list"
    main = kept(ctx, "PASS")
    assert laya_verdict(ctx, MODEL).test_id == main.id
    kept(ctx, "FAIL", watchlist="picked")
    kept(ctx, "FAIL", start=SECOND)
    v = laya_verdict(ctx, MODEL)
    assert v.useful and v.test_id == main.id, "a newer look on another list or a later start is not the verdict"
    kept(ctx, "FAIL")
    kept(ctx, "PASS", watchlist="picked")
    kept(ctx, "PASS", start=SECOND)
    assert laya_verdict(ctx, MODEL).verdict == "FAIL", "a pass on a hand-picked list or span never gives weight"
    unknown = json.loads(ctx.conn.execute("SELECT result FROM laya_tests WHERE id = ?", (main.id,)).fetchone()[0])
    unknown.pop("watchlist")
    ctx.conn.execute("DELETE FROM laya_tests")
    ctx.conn.execute("INSERT INTO laya_tests (run, model, verdict, result, run_at) VALUES ('main', ?, 'PASS', ?, ?)",
                     (MODEL, json.dumps(unknown), unknown["run_at"]))
    assert laya_verdict(ctx, MODEL).verdict is None, "a result with no list recorded never gives weight"


def test_the_live_score_is_the_one_for_the_fill_day_and_reads_only_what_is_known_at_the_tick(ctx):
    market(ctx, signal=True)
    score_history(ctx, scorer=FakeLaya())
    now = datetime(2024, 3, 13, 22, 0, tzinfo=timezone.utc)
    day, fill = date(2024, 3, 12), date(2024, 3, 14)
    """A Wednesday-evening tick reads Tuesday's bar; its orders fill at Thursday's open."""
    live = laya_live(ctx, ["GM", "F"], TEST_START, day, now, model=MODEL)
    full = daily_scores(panel_of(ctx, end=fill), TEST_START, fill)
    known = daily_scores(build_panel(ctx, model=MODEL, start=FIRST, end=fill, research=True,
                                     known_by=now.isoformat()), TEST_START, fill)
    for s in ("GM", "F"):
        thursday = dict(known[s])[fill - timedelta(days=1)]
        assert live.scores[s][-1] == (day, thursday), "Thursday's decision score, from what the tick knows"
        assert thursday != dict(full[s])[day], "not Wednesday's, which the backtest's Tuesday bar would read"
        assert list(live.scores[s][:-1]) == [r for r in full[s] if r[0] < day], "earlier days dated as the backtest"
    late = backfill(ctx, "GM", [("General Motors shares rise sharply", date(2024, 3, 13))])
    ctx.conn.execute("UPDATE articles SET published_at = '2024-03-13T23:30:00+00:00' WHERE id = ?", (late[0],))
    score_history(ctx, scorer=FakeLaya())
    after = daily_scores(panel_of(ctx, end=fill), TEST_START, fill)
    assert dict(after["GM"])[fill - timedelta(days=1)] != dict(full["GM"])[fill - timedelta(days=1)], \
        "the late headline is usable by Thursday's open"
    assert laya_live(ctx, ["GM", "F"], TEST_START, day, now, model=MODEL).scores == live.scores, \
        "known after the tick, so the live step never reads it"


def test_a_useful_laya_weighs_as_the_mean_of_each_moods_price_methods_and_otherwise_0():
    settings = RegimeSettings(min_probability=0.6)
    got = {m: round(r[METHOD], 4) for m, r in with_laya(settings, True).weights.items()}
    assert got == {"calm_up": 0.125, "volatile_up": 0.1375, "calm_down": 0.1625, "volatile_down": 0.15}
    assert all(r[METHOD] == 0 for r in with_laya(settings, False).weights.values())
    assert all({k: v for k, v in r.items() if k != METHOD} == PRESETS[m]
               for m, r in with_laya(settings, True).weights.items())
    alone = laya_only(settings).weights
    assert all(r[METHOD] == 1 and sum(r.values()) == 1 for r in alone.values())


def test_the_verdict_note_names_the_weight():
    failed = LayaVerdict("FAIL", False, MODEL, 3, "2026-10-01T00:00:00Z", "FAIL: weight 0")
    assert verdict_note(failed) == "Laya's predictive test: FAIL (test 3, 2026-10-01); FAIL: weight 0 in core-sat"
    none = LayaVerdict(None, False, MODEL, why="no predictive test kept for this model: weight 0")
    assert verdict_note(none).endswith("none kept yet; no predictive test kept for this model: weight 0 in core-sat")
    passed = LayaVerdict("PASS", True, MODEL, 4, "2026-10-01T00:00:00Z", "passed and beat both baselines")
    assert "calm_up 0.125" in verdict_note(passed, RegimeSettings(min_probability=0.6))


def bearish_on(symbol, start, end):
    """A Laya score of -1 for `symbol` on every day: a weighted Laya keeps the satellite out of it."""
    days = [start + timedelta(days=k) for k in range((end - start).days + 1)]
    return {symbol: tuple((d, -1.0) for d in days), "SLOW": tuple((d, 1.0) for d in days)}


def laya_backtest(make_ctx, monkeypatch, useful, name):
    ctx = make_ctx({"UP": BARS, "SLOW": rising(400, "SLOW", daily=0.001)}, name)
    stored(ctx, rising(400, "MKT", daily=0.002))
    verdict = LayaVerdict("PASS" if useful else "FAIL", useful, MODEL, 1, "2026-10-01T00:00:00Z")
    monkeypatch.setattr("oracle.services.backtest_service.laya_input",
                        lambda *a, **k: LayaInput(bearish_on("UP", BARS[0].date, BT_END), verdict))
    return run_backtest(ctx, "tech", BT_START, BT_END, commission=2.0, walk_forward=True)


def test_at_weight_0_core_sat_laya_is_core_sat_exactly_in_the_backtest_and_walk_forward(make_ctx, monkeypatch):
    r = laya_backtest(make_ctx, monkeypatch, False, "a.db")
    lines = {x.name: x for x in r.lines}
    plain, laya = lines["core_sat"], lines["core_sat_laya"]
    assert laya.label == "Core-sat-laya" and laya.trades >= 2
    assert (laya.points, laya.trades, laya.costs, laya.max_fall) == (plain.points, plain.trades, plain.costs, plain.max_fall)
    walked = {x.name: x for x in r.walk_forward.lines}
    assert walked["core_sat_laya"].segments == walked["core_sat"].segments
    assert laya.caption.startswith("Core-sat-laya: core-sat with Laya's daily news score as a satellite method (2 "
                                   "companies scored")
    assert "FAIL" in laya.caption and "weight 0 in core-sat" in laya.caption and "core-sat's exactly" in laya.caption


def test_a_useful_laya_moves_the_satellite_once_the_satellite_has_weight(make_ctx, monkeypatch):
    monkeypatch.setattr("oracle.broker.core_sat.SATELLITE_SHARE", 0.25)
    plain = {x.name: x for x in laya_backtest(make_ctx, monkeypatch, False, "a.db").lines}
    weighed = {x.name: x for x in laya_backtest(make_ctx, monkeypatch, True, "b.db").lines}
    assert weighed["core_sat"] == plain["core_sat"], "core_sat never reads Laya"
    assert weighed["core_sat_laya"].points != plain["core_sat_laya"].points
    assert "weighs in core-sat's satellite (calm_up 0.125" in weighed["core_sat_laya"].caption
