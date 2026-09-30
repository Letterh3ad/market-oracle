"""Laya over the backfilled history and the pre-registered predictive test: pairs scored per target,
relations counted point in time, no look-ahead, and the fixed bar on synthetic markets where the
news does and does not lead the price."""

import random
import time as time_module
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from oracle.config import AnalystsConfig, Config
from oracle.market.bars import Bar
from oracle.research.news import Article
from oracle.services.activity_service import PREEMPTED_BY, JobRunner, retry, submit_laya_history, submit_laya_test
from oracle.services.context import open_context
from oracle.services.laya_features import known_day
from oracle.services.errors import JobAlreadyRunning
from oracle.services.jobs_service import single_flight
from oracle.services.laya_history_service import (
    HISTORY_WAITS_FOR, history_pairs, last_history_run, plan_history, score_history,
)
from oracle.services.jobs_service import job_statuses
from oracle.services.laya_service import live_model_work
from oracle.services.laya_service import SharedScorer, laya_scores
from oracle.services.laya_test_service import (
    HORIZONS, MAIN_RELATIONS, Panel, build_panel, horizon_result, keep_laya_test, laya_tests, run_laya_test,
)
from oracle.analysts.predictive import lookback
from oracle.broker.walk_forward import segments
from oracle.services.links_service import add_link
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from oracle.store.articles import ArticleRepo
from oracle.store.bars import BarRepo
from oracle.store.history import HistoryRepo, Window
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.laya import FakeLaya
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
MODEL = "fake-laya"
AUTOS = dict(sector="Consumer Discretionary", industry_group="Automobiles & Components", currency="USD")
MAINS = [("HMC", "Honda Motor Co."), ("GM", "General Motors"), ("F", "Ford Motor"), ("TM", "Toyota Motor"),
         ("STLA", "Stellantis"), ("RIVN", "Rivian Automotive")]
FIRST, LAST = date(2022, 8, 1), date(2024, 6, 28)
END = date(2024, 3, 20)
"""Three segments: the first has no trailing fit, so two are counted."""


@pytest.fixture
async def ctx(tmp_path):
    rows = [equity(s, n, market_cap="Large Cap", industry="Automobiles", **AUTOS) for s, n in MAINS]
    # Another sector, so it is on HMC's map only through the link.
    rows.append(equity("BWA", "BorgWarner", market_cap="Mid Cap", industry="Machinery", sector="Industrials",
                       industry_group="Capital Goods", currency="USD"))
    # A Tokyo listing on no map, watched only by the tests that need it.
    rows.append(equity("8058.T", "Mitsubishi Corporation", country="Japan", exchange="JPX", market_cap="Large Cap",
                       industry="Trading Companies", sector="Industrials", industry_group="Capital Goods",
                       currency="JPY"))
    web =FakeWeb(FakeFinanceDatabase(rows), FakeYahoo())
    with open_context(Config(db_path=tmp_path / "o.db", analysts=AnalystsConfig(peers=5)), None, FakeClock(NOW),
                      web.transport) as c:
        await refresh_universe(c)
        for s, _ in MAINS:
            watch(c, s)
        add_link(c, "HMC", "BWA", "partner")
        yield c


def weekdays(a, b):
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def noon(d):
    return datetime.combine(d, time(12), timezone.utc)


def backfill(ctx, symbol, items):
    """Store (title, published day) as `symbol`'s backfilled news; its ids."""
    ids = ArticleRepo(ctx.conn).save([Article(f"https://n.test/{symbol}/{t}", t, "Wire", noon(d)) for t, d in items],
                                     "google_news", "2026-09-30T00:00:00+00:00")
    HistoryRepo(ctx.conn).finish(Window(symbol, "google_news", date(2022, 9, 1), date(2024, 7, 1), len(ids), len(ids),
                                        0, 0), ids, "2026-09-30T00:00:00+00:00")
    return ids


def market(ctx, *, signal, seed=7):
    """Each main company gets news on about a third of days; with `signal` its price drifts the
    news' way for the next 8 bars from the next open, otherwise the price ignores it."""
    rng = random.Random(seed)
    days = weekdays(FIRST, LAST)
    for symbol, name in MAINS:
        price, left, way, news, bars = 100.0, 0, 0, [], []
        for k, d in enumerate(days):
            drift = 0.004 * way if left > 0 else 0.0
            left -= 1
            opened = price
            price = opened * (1 + drift + rng.gauss(0, 0.01))
            bars.append(Bar(symbol, d, opened, max(opened, price), min(opened, price), price, price, 1000, "USD", "t"))
            if rng.random() < 0.35:
                up = rng.random() < 0.5
                news.append((f"{name} shares {'rise' if up else 'fall'} {k}", d))
                if signal:
                    left, way = 8, 1 if up else -1
        BarRepo(ctx.conn).upsert(bars, "2026-09-30T00:00:00+00:00")
        backfill(ctx, symbol, news)


def test_pairs_are_each_targets_own_backfill_first_then_its_map_companies_backfill(ctx):
    backfill(ctx, "HMC", [("Honda shares rise", date(2023, 1, 3))])
    backfill(ctx, "GM", [("GM shares fall", date(2023, 1, 4))])
    backfill(ctx, "BWA", [("BorgWarner shares rise", date(2023, 1, 5))])
    groups = history_pairs(ctx)
    assert [g.relation for g in groups[:len(MAINS)]] == ["own"] * len(MAINS), "every own group before any map group"
    hmc = {g.subject: g.relation for g in groups if g.target == "HMC"}
    assert hmc["HMC"] == "own" and hmc["GM"] == "competitor" and hmc["BWA"] == "industry", "a main-list rival counts"
    assert {g.subject for g in groups if g.target == "GM"} >= {"GM", "HMC"}
    assert not any(g.target == "BWA" for g in groups), "only main-list companies are targets"


def test_scoring_skips_scored_pairs_so_a_rerun_resumes_and_measures_pairs(ctx):
    backfill(ctx, "HMC", [("Honda shares rise", date(2023, 1, 3))])
    backfill(ctx, "GM", [("GM shares fall", date(2023, 1, 4))])
    plan = plan_history(ctx)
    assert plan.unscored == plan.pairs == 2 * len(MAINS) - 1 + 1, "each article for its company and every rival"
    first = score_history(ctx, scorer=FakeLaya())
    assert first.scored == plan.pairs and first.stopped is None and first.groups_done == first.groups
    again = score_history(ctx, scorer=FakeLaya())
    assert again.scored == 0 and again.already == first.scored
    assert plan_history(ctx, model=MODEL).unscored == 0
    assert last_history_run(ctx).scored == 0
    gm = {s.title: s.relation for s in laya_scores(ctx, "GM", model=MODEL)}
    assert gm == {"GM shares fall": "own", "Honda shares rise": "competitor"}


def test_a_cancel_stops_between_groups_and_live_work_closes_the_shared_model(ctx, monkeypatch):
    backfill(ctx, "HMC", [("Honda shares rise", date(2023, 1, 3))])
    backfill(ctx, "GM", [("GM shares fall", date(2023, 1, 4))])
    assert score_history(ctx, scorer=FakeLaya(), stop=lambda: True).stopped == "cancelled"
    fake = FakeLaya()
    shared = SharedScorer(lambda: fake, tag=MODEL)
    monkeypatch.setattr("oracle.services.laya_service.wait", lambda s: None)
    calls = iter([None, "research"] + [None] * 1000)
    run = score_history(ctx, scorer=shared, busy=lambda: next(calls))
    assert run.scored > 0 and fake.closed and shared.loads == 2, "closed while research ran, loaded again after"


def loads(monkeypatch, *devices):
    """Each model load gives the next device; the models handed out, to check which were closed."""
    made, left = [], list(devices)

    def load(c):
        made.append(FakeLaya(MODEL, left.pop(0) if len(left) > 1 else left[0]))
        return made[-1]

    monkeypatch.setattr("oracle.services.laya_service.load_scorer", load)
    monkeypatch.setattr("oracle.services.laya_service.wait", lambda s: None)
    return made


def test_a_model_that_loads_on_the_cpu_is_closed_and_the_run_waits_for_gpu_room(ctx, monkeypatch):
    backfill(ctx, "HMC", [("Honda shares rise", date(2023, 1, 3))])
    made = loads(monkeypatch, "cpu", "cpu", "cuda")
    said = []
    run = score_history(ctx, on_progress=said.append)
    assert run.scored == len(MAINS) and run.device == "cuda"
    assert [m.device for m in made] == ["cpu", "cpu", "cuda"] and made[0].closed and made[1].closed
    assert any("waiting for GPU room" in line for line in said)


def test_the_cpu_is_used_only_when_allowed_or_set_in_settings(ctx, monkeypatch):
    backfill(ctx, "HMC", [("Honda shares rise", date(2023, 1, 3))])
    loads(monkeypatch, "cpu")
    assert score_history(ctx, allow_cpu=True).device == "cpu"
    backfill(ctx, "GM", [("GM shares fall", date(2023, 1, 4))])
    ctx.config = replace(ctx.config, laya=replace(ctx.config.laya, device="cpu"))
    assert score_history(ctx).device == "cpu"


def test_a_cancel_while_waiting_for_gpu_room_stops_the_run(ctx, monkeypatch):
    backfill(ctx, "HMC", [("Honda shares rise", date(2023, 1, 3))])
    loads(monkeypatch, "cpu")
    asked = iter([False, False] + [True] * 100)
    run = score_history(ctx, stop=lambda: next(asked))
    assert run.stopped == "cancelled" and run.scored == 0


def test_the_lock_stays_alive_through_a_long_wait_for_gpu_room(ctx, monkeypatch):
    backfill(ctx, "HMC", [("Honda shares rise", date(2023, 1, 3))])
    made = loads(monkeypatch, *["cpu"] * 5, "cuda")
    fresh = []

    def later(seconds):
        ctx.clock.advance(600)
        fresh.append(next(x for x in job_statuses(ctx) if x.job == "laya_history").stale_reason is None)

    monkeypatch.setattr("oracle.services.laya_service.wait", later)
    run = score_history(ctx)
    assert run.scored == len(MAINS) and len(fresh) > 30 and all(fresh), "held past the 1800 s lease"
    assert made[-1].device == "cuda"


def test_with_no_cuda_gpu_at_all_it_stops_and_says_so_unless_the_cpu_is_allowed(ctx, monkeypatch):
    backfill(ctx, "HMC", [("Honda shares rise", date(2023, 1, 3))])
    made = loads(monkeypatch, "cpu")
    monkeypatch.setattr(FakeLaya, "why", "no CUDA GPU; on the CPU", raising=False)
    run = score_history(ctx)
    assert run.stopped.startswith("no CUDA GPU") and "allow the CPU" in run.stopped and run.scored == 0
    assert len(made) == 1 and made[0].closed
    assert score_history(ctx, allow_cpu=True).scored == len(MAINS)


def test_one_history_run_at_a_time_across_processes_and_it_yields_to_model_work_anywhere(ctx):
    with single_flight(ctx, "laya_history", 60):
        with pytest.raises(JobAlreadyRunning):
            score_history(ctx, scorer=FakeLaya())
    assert live_model_work(ctx, HISTORY_WAITS_FOR) is None
    for lock, name in (("tick", "tick"), ("industry_research", "industry research"), ("research", "research")):
        with single_flight(ctx, lock, 60):
            assert live_model_work(ctx, HISTORY_WAITS_FOR) == name
            assert live_model_work(ctx) == (name if lock == "research" else None), "industry's own set"
    assert "industry" in PREEMPTED_BY


def test_the_measured_time_a_pair_leaves_out_waiting_for_live_work(ctx, monkeypatch):
    backfill(ctx, "HMC", [("Honda shares rise", date(2023, 1, 3))])
    monkeypatch.setattr("oracle.services.laya_service.wait", lambda s: time_module.sleep(0.3))
    calls = iter([None, "research"] + [None] * 1000)
    run = score_history(ctx, scorer=FakeLaya(), busy=lambda: next(calls))
    assert run.seconds >= 0.3 and run.score_seconds < 0.2


def test_a_link_counts_only_from_when_it_was_first_known_and_03_links_wait_for_the_sensitivity_run(ctx):
    ctx.conn.execute("UPDATE company_links SET first_seen = '2023-06-01T00:00:00+00:00'")
    ctx.conn.execute("INSERT INTO company_links (symbol, other, relation, source, first_seen, last_seen) "
                     "VALUES ('GM', 'BWA', 'supplier', 'filing', '2022-01-01T00:00:00+00:00', '2022-01-01T00:00:00+00:00')")
    backfill(ctx, "BWA", [("BorgWarner shares rise early", date(2023, 5, 1)),
                          ("BorgWarner shares rise late", date(2023, 7, 3))])
    score_history(ctx, scorer=FakeLaya())
    main = build_panel(ctx, model=MODEL)
    assert main.daily["HMC"] == {date(2023, 7, 3): {"industry": (1, 0.8, 0.2)}}, "known 06-01: only the later article"
    assert main.daily["GM"] == {}, "a filing-typed supplier link is left out of the main run"
    sens = build_panel(ctx, model=MODEL, sensitivity=True)
    assert set(sens.daily["HMC"]) == {date(2023, 5, 1), date(2023, 7, 3)}, "links as known today"
    assert sens.daily["GM"][date(2023, 5, 1)] == {"supplier": (1, 0.8, 0.2)}


def test_future_articles_change_no_feature_before_their_day(ctx):
    backfill(ctx, "HMC", [("Honda shares rise", date(2023, 3, 1))])
    score_history(ctx, scorer=FakeLaya())
    before = build_panel(ctx, model=MODEL).daily["HMC"]
    backfill(ctx, "HMC", [("Honda shares fall", date(2023, 3, 6)), ("Honda shares fall later", date(2023, 4, 1))])
    score_history(ctx, scorer=FakeLaya())
    after = build_panel(ctx, model=MODEL).daily["HMC"]
    for day in weekdays(date(2023, 2, 1), date(2023, 3, 5)):
        assert lookback(after, day, 28, MAIN_RELATIONS) == lookback(before, day, 28, MAIN_RELATIONS), day
    assert lookback(after, date(2023, 3, 7), 7, MAIN_RELATIONS)["own_sum"] == pytest.approx(0.0), "0.8 - 0.8"


def test_an_sec_filing_dated_at_midnight_acts_from_the_next_session(ctx):
    [i] = ArticleRepo(ctx.conn).save([Article("https://sec.test/8k", "Honda 8-K", "SEC",
                                              datetime(2023, 3, 1, tzinfo=timezone.utc))], "sec_edgar", "x")
    HistoryRepo(ctx.conn).finish(Window("HMC", "sec_edgar", date(2022, 9, 1), date(2024, 7, 1), 1, 1, 0, 0), [i], "x")
    score_history(ctx, scorer=FakeLaya())
    daily = build_panel(ctx, model=MODEL).daily["HMC"]
    assert lookback(daily, date(2023, 3, 1), 7, MAIN_RELATIONS)["own_relevance"] == 0.0
    assert lookback(daily, date(2023, 3, 2), 7, MAIN_RELATIONS)["own_relevance"] == pytest.approx(0.9)


def tokyo_news(ctx, items):
    """(title, stamp) as 8058.T's backfilled news."""
    ids = ArticleRepo(ctx.conn).save([Article(f"https://n.test/8058/{t}", t, "Wire", at) for t, at in items],
                                     "google_news", "x")
    HistoryRepo(ctx.conn).finish(Window("8058.T", "google_news", FIRST, LAST, len(ids), len(ids), 0, 0), ids, "x")


def test_a_date_only_google_stamp_reaches_a_tokyo_listing_a_day_after_a_new_york_one(ctx):
    watch(ctx, "8058.T")
    stamp = datetime(2023, 7, 3, 7, tzinfo=timezone.utc)
    tokyo_news(ctx, [("Mitsubishi shares rise", stamp)])
    backfill_at(ctx, "HMC", [("Honda shares rise", stamp)])
    score_history(ctx, scorer=FakeLaya())
    daily = build_panel(ctx, model=MODEL).daily
    assert list(daily["HMC"]) == [date(2023, 7, 4)], "known 07-04 07:00 UTC, before New York's open"
    assert list(daily["8058.T"]) == [date(2023, 7, 5)], "after Tokyo's open on the 4th"
    assert lookback(daily["8058.T"], date(2023, 7, 4), 7, MAIN_RELATIONS)["own_sum"] == 0.0
    assert lookback(daily["8058.T"], date(2023, 7, 5), 7, MAIN_RELATIONS)["own_sum"] == pytest.approx(0.8)


def test_a_headline_about_a_tokyo_session_stamped_midnight_pacific_before_it_never_feeds_that_session(ctx):
    """The gate's leak: news written during the Tokyo session is stamped the Pacific day before."""
    watch(ctx, "8058.T")
    rng = random.Random(3)
    days = weekdays(date(2023, 1, 9), date(2023, 6, 30))
    way = {d: rng.choice(("rise", "fall")) for d in days}
    pacific = ZoneInfo("America/Los_Angeles")
    tokyo_news(ctx, [(f"Mitsubishi shares {way[d]} {d}", datetime.combine(d - timedelta(days=1), time(0), pacific))
                     for d in days])
    score_history(ctx, scorer=FakeLaya())
    daily = build_panel(ctx, model=MODEL).daily["8058.T"]
    for d in days:
        before = d - timedelta(days=1)
        want = {"own": (1, 0.8 if way[before] == "rise" else -0.8, 0.9)} if before in way else None
        assert daily.get(d) == want, "a session's own headline first feeds the next one"
    hits = sum(1 for d in days if (x := lookback(daily, d, 1, MAIN_RELATIONS)["own_sum"])
               and (x > 0) == (way[d] == "rise"))
    assert hits < 0.65 * len(days), "no better than chance at the session it describes"


def backfill_at(ctx, symbol, items):
    ids = ArticleRepo(ctx.conn).save([Article(f"https://n.test/{symbol}/{t}", t, "Wire", at) for t, at in items],
                                     "google_news", "x")
    HistoryRepo(ctx.conn).finish(Window(symbol, "google_news", FIRST, LAST, len(ids), len(ids), 0, 0), ids, "x")


def test_a_score_that_is_only_attention_with_its_sign_flipped_never_beats_the_baseline():
    """News names fall and Laya reads their news as down: own_mean is minus attention, ranked alike."""
    rng = random.Random(5)
    mains = [f"S{i}" for i in range(8)]
    days = weekdays(FIRST, LAST)
    daily, bars = {m: {} for m in mains}, {}
    for m in mains:
        price, rows, news = 100.0, [], set()
        for d in days:
            if d.weekday() == 0 and rng.random() < 0.5:  # at most one article in any 7-day window
                news.add(d)
                daily[m][d] = {"own": (1, -0.8, 0.2)}
        for d in days:
            opened = price
            hit = any(d - timedelta(days=k) in news for k in range(7))
            price = opened * (1 + (-0.004 if hit else 0.004) + rng.gauss(0, 0.01))
            rows.append(Bar(m, d, opened, max(opened, price), min(opened, price), price, price, 1, "USD", "t"))
        bars[m] = rows
    panel = Panel(mains, daily, bars, {m: [] for m in mains})
    r = horizon_result(panel, 5, 7, segments(date(2022, 9, 21), END), MAIN_RELATIONS, None)
    assert r.baseline_raw_ic < 0, "news names fall: attention predicts backwards"
    assert r.ic > 0 and r.segments[1].weights["own_mean"] > 0
    assert r.baseline_ic == pytest.approx(r.ic) and r.beats_baseline is False, "same ranks, fitted the same way"


def test_future_bars_and_articles_change_no_segment_that_ended_before_them(ctx):
    market(ctx, signal=True)
    score_history(ctx, scorer=FakeLaya())
    panel = build_panel(ctx, model=MODEL, end=END)
    spans = segments(date(2022, 9, 21), END)
    full = horizon_result(panel, 5, 7, spans, MAIN_RELATIONS, None)
    cut = spans[1][1] + timedelta(days=10)
    short = replace(panel, bars={s: [b for b in bars if b.date <= cut] for s, bars in panel.bars.items()},
                    daily={s: {d: v for d, v in days.items() if d <= cut} for s, days in panel.daily.items()})
    again = horizon_result(short, 5, 7, spans, MAIN_RELATIONS, None)
    assert again.segments[:2] == full.segments[:2]
    assert again.segments[2] != full.segments[2]


def test_news_that_leads_the_price_passes_the_bar_and_the_result_is_kept(ctx):
    market(ctx, signal=True)
    score_history(ctx, scorer=FakeLaya())
    t = run_laya_test(ctx, model=MODEL, end=END)
    assert [h.horizon for h in t.horizons] == [h for h, _ in HORIZONS]
    five = t.horizons[0]
    assert [s.counted for s in five.segments] == [False, True, True]
    assert five.segments[0].why == "no trailing fit"
    assert five.segments[1].weights["own_mean"] > 0, "fitted on the first segment's days only"
    assert set(five.segments[1].weights) == {"own_mean", "competitor_mean", "industry_mean"}, "relevance left out"
    assert five.baseline_ic is not None and five.beats_baseline, "direction beats news attention alone"
    assert five.verdict == "PASS" and five.ic > 0 and five.t > 2 and five.positive == 2
    assert {f.name for f in five.features if not f.in_score} == {"own_sum", "competitor_sum", "industry_sum",
                                                                 "own_relevance", "own_attention"}
    assert t.companies == len(MAINS) and t.articles["own"] > 0 and "Survivorship" in t.caveat
    kept = keep_laya_test(ctx, t)
    assert laya_tests(ctx) == [kept] and kept.id is not None and kept.horizons == t.horizons


def test_news_the_price_ignores_does_not_pass(ctx):
    market(ctx, signal=False)
    score_history(ctx, scorer=FakeLaya())
    t = run_laya_test(ctx, model=MODEL, end=END)
    assert not t.passed and t.verdict in ("FAIL", "INCONCLUSIVE")


def test_a_thin_segment_is_named_and_left_out(ctx):
    market(ctx, signal=True)
    ctx.conn.execute("DELETE FROM history_articles WHERE article_id IN (SELECT id FROM articles WHERE "
                     "published_at >= '2023-10-01' AND published_at < '2024-04-01')")
    score_history(ctx, scorer=FakeLaya())
    t = run_laya_test(ctx, model=MODEL, end=END)
    last = t.horizons[0].segments[2]
    assert not last.counted and last.why.startswith("thin coverage"), last.why
    assert t.horizons[0].verdict == "INCONCLUSIVE", "one counted segment is too few"


async def test_jobs_score_and_test_and_retry_rebuilds_them(ctx, monkeypatch):
    backfill(ctx, "HMC", [("Honda shares rise", date(2023, 1, 3))])
    monkeypatch.setattr("oracle.services.laya_service.load_scorer", lambda c: FakeLaya(MODEL, "cuda"))
    runner = JobRunner(ctx)
    job = submit_laya_history(ctx, runner)
    done = await runner.wait(job.id)
    assert done.state == "done" and done.result.scored == len(MAINS)
    t = await runner.wait(submit_laya_test(ctx, runner, sensitivity=True, model=MODEL).id)
    assert t.state == "done" and t.result.run == "sensitivity" and laya_tests(ctx)[0].id == t.result.id
    for submit in (lambda: submit_laya_history(ctx, runner), lambda: submit_laya_history(ctx, runner, allow_cpu=True),
                   lambda: submit_laya_test(ctx, runner, model=MODEL)):
        job = submit()
        runner.cancel(job.id)
        await runner.wait(job.id)
        again = retry(ctx, runner, job.id)
        assert (again.kind, again.subject, again.label) == (job.kind, job.subject, job.label) and again.id != job.id
        await runner.wait(again.id)
    await runner.stop()
