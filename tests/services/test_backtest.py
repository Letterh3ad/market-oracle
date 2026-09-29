from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.analysts.signals import AnalystSignal
from oracle.broker.regime import MOODS
from oracle.broker.walk_forward import segments
from oracle.config import Config, StrategyConfig
from oracle.services.activity_service import JobRunner, retry, submit_backtest
from oracle.services.backtest_service import (
    KEEP, check_backtest, keep_backtest, kept_backtest, kept_backtests, run_backtest, stored_span,
)
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, NoPrices, UnknownBacktest, UnknownWatchlist
from oracle.services.paper_service import place_buy
from oracle.services.watch_service import watch
from oracle.store.bars import BarRepo
from oracle.store.meta import MetaRepo
from oracle.store.signals import SignalRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.listings import Listing
from tests.broker.test_backtest import rising
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)


def seed(c, bars_by_symbol):
    UniverseRepo(c.conn).replace_all([
        Listing(s, f"{s} Inc", "Equity", "United States", "NMS", currency="USD", company_key=s.lower(), is_primary=True)
        for s in bars_by_symbol])
    MetaRepo(c.conn).set("universe_refreshed_at", "2026-09-24T00:00:00Z")
    for s, bars in bars_by_symbol.items():
        if bars:
            BarRepo(c.conn).upsert(bars, "2026-09-24T00:00:00Z")
        watch(c, s, "tech")


@pytest.fixture
def make_ctx(tmp_path):
    opened = []

    def make(bars_by_symbol, name="o.db", **config):
        manager = open_context(Config(db_path=tmp_path / name, base_currency="USD", **config), None, FakeClock(NOW))
        opened.append(manager)
        c = manager.__enter__()
        seed(c, bars_by_symbol)
        return c

    yield make
    for m in opened:
        m.__exit__(None, None, None)


BARS = rising(400)
START, END = BARS[300].date, BARS[-1].date


def test_a_trending_list_is_bought_and_set_against_buy_and_hold(make_ctx):
    ctx = make_ctx({"UP": BARS})
    r = run_backtest(ctx, "TECH", START, END)
    assert r.watchlist == "tech" and r.listings == ("UP",)
    strategy, held, momentum, *_ = r.lines
    assert (strategy.name, held.name, momentum.name) == ("strategy", "buy_and_hold", "momentum")
    assert strategy.trades == len(r.trades) >= 1 and r.trades[0].symbol == "UP"
    assert strategy.points[0] == (START, 0.0) and held.points[0] == (START, 0.0)
    assert held.return_ > strategy.return_ > 0
    assert strategy.costs == pytest.approx(sum(t.costs for t in r.trades))
    text = " ".join(r.notes)
    assert "survivorship" in text and "only today's" in text and "10 basis points" in text


def test_the_momentum_line_holds_the_leader_under_the_same_costs(make_ctx):
    # A year of history before the start, so the first month end ranks both.
    up, slow = rising(400), rising(400, "SLOW", daily=0.001)
    r = run_backtest(make_ctx({"UP": up, "SLOW": slow}), "tech", up[300].date, END, commission=2.0)
    momentum = r.lines[2]
    assert momentum.label == "Momentum" and momentum.trades >= 2 and momentum.costs >= 2 * 2.0
    assert momentum.points[0] == (START, 0.0) and momentum.return_ > 0
    assert "Momentum trades under the strategy's rules" in " ".join(r.notes)



def test_a_momentum_buy_that_cannot_fill_is_named(make_ctx):
    big = [replace(b, open=b.open * 1000, high=b.high * 1000, low=b.low * 1000, close=b.close * 1000,
                   adj_close=None if b.adj_close is None else b.adj_close * 1000) for b in rising(400, "BIG")]
    r = run_backtest(make_ctx({"BIG": big, "SLOW": rising(400, "SLOW", daily=0.001)}), "tech", big[300].date,
                     big[-1].date)
    assert any(n.startswith("Momentum could not fill: ") and "BIG" in n and "no whole share" in n for n in r.notes)

def test_a_backtest_changes_nothing_stored(make_ctx):
    ctx = make_ctx({"UP": BARS})
    place_buy(ctx, "UP", 500.0)
    before = list(ctx.conn.iterdump())
    run_backtest(ctx, "tech", START, END)
    assert list(ctx.conn.iterdump()) == before


def test_bars_after_the_last_day_are_never_seen(make_ctx):
    later = rising(460)[400:]
    crashed = [replace(b, open=b.open / 3, close=b.close / 3, adj_close=b.close / 3) for b in later]
    a = run_backtest(make_ctx({"UP": BARS}, "a.db"), "tech", START, END)
    b = run_backtest(make_ctx({"UP": BARS + crashed}, "b.db"), "tech", START, END)
    assert a == b


def test_bad_requests_are_refused(make_ctx):
    ctx = make_ctx({"UP": BARS})
    with pytest.raises(InvalidQuery, match="before"):
        run_backtest(ctx, "tech", END, START)
    with pytest.raises(InvalidQuery, match="slippage"):
        run_backtest(ctx, "tech", START, END, slippage_bps=-1)
    with pytest.raises(InvalidQuery, match="commission"):
        run_backtest(ctx, "tech", START, END, commission=float("nan"))
    with pytest.raises(UnknownWatchlist):
        run_backtest(ctx, "nope", START, END)
    with pytest.raises(NoPrices):
        run_backtest(make_ctx({"NEW": []}, "empty.db"), "tech", START, END)


def test_days_outside_the_stored_prices_are_refused(make_ctx):
    ctx = make_ctx({"UP": BARS})
    first, last = BARS[0].date, BARS[-1].date
    assert stored_span(ctx, "TECH") == ("tech", first, last)
    for start, end in ((date(2000, 1, 3), END), (START, last + timedelta(days=1))):
        with pytest.raises(InvalidQuery, match=f"stored from {first.isoformat()} to {last.isoformat()}"):
            run_backtest(ctx, "tech", start, end)
    with pytest.raises(InvalidQuery, match="before"):
        check_backtest(ctx, "tech", END, START, 10, 0)


def test_listings_without_prices_in_range_are_named(make_ctx):
    ctx = make_ctx({"UP": BARS, "NEW": []})
    r = run_backtest(ctx, "tech", START, END)
    assert r.listings == ("UP",)
    assert any("NEW" in n and "no stored prices" in n for n in r.notes)


def test_a_listing_with_no_exchange_rate_is_never_traded_and_is_named(make_ctx):
    pounds = [replace(b, symbol="LON", currency="GBP") for b in rising(400, "LON")]
    ctx = make_ctx({"UP": BARS, "LON": pounds})
    r = run_backtest(ctx, "tech", START, END)
    assert r.trades and all(t.symbol == "UP" for t in r.trades)
    assert any("LON" in n and "no USD exchange rate" in n for n in r.notes)
    assert "of the 1 listing," in r.lines[1].caption


def test_a_threshold_above_what_a_technicals_lean_can_reach_is_called_out(make_ctx):
    ctx = make_ctx({"UP": BARS}, strategy=StrategyConfig(min_probability=0.7))
    r = run_backtest(ctx, "tech", START, END)
    assert r.trades == ()
    assert any("62.5%" in n and "cannot buy" in n for n in r.notes)


def test_a_kept_run_reads_back_whole_with_its_currency(make_ctx):
    ctx = make_ctx({"UP": BARS})
    r = run_backtest(ctx, "tech", START, END, 5, 1)
    saved = keep_backtest(ctx, r)
    assert (saved.base_currency, saved.ran_at, saved.backtest) == ("USD", NOW, r)
    assert kept_backtests(ctx) == [saved] and kept_backtest(ctx, saved.id) == saved
    with pytest.raises(UnknownBacktest):
        kept_backtest(ctx, saved.id + 1)


def test_only_the_newest_runs_are_kept(make_ctx):
    ctx = make_ctx({"UP": BARS})
    r = run_backtest(ctx, "tech", START, END)
    ids = [keep_backtest(ctx, r).id for _ in range(KEEP + 2)]
    assert [s.id for s in kept_backtests(ctx)] == ids[::-1][:KEEP]


@pytest.fixture
async def runner(make_ctx):
    ctx = make_ctx({"UP": BARS})
    r = JobRunner(ctx)
    yield ctx, r
    await r.stop()


async def test_a_backtest_job_runs_keeps_its_result_and_joins_the_same_request(runner):
    ctx, r = runner
    job = submit_backtest(ctx, r, "TECH", START, END, 5, 1)
    assert submit_backtest(ctx, r, "tech", START, END, 5.0, 1.0) is job
    assert (job.kind, job.link) == ("backtest", "#ledgers")
    assert job.label == f"Backtest tech, {START.isoformat()} to {END.isoformat()}"
    await r.wait(job.id)
    assert job.state == "done", job.error
    assert kept_backtests(ctx) == [job.result]
    assert (job.result.backtest.slippage_bps, job.result.backtest.commission) == (5, 1)


async def test_bad_days_are_refused_before_a_job_is_queued(runner):
    ctx, r = runner
    with pytest.raises(InvalidQuery, match="before"):
        submit_backtest(ctx, r, "tech", END, START)
    with pytest.raises(InvalidQuery, match="stored from"):
        submit_backtest(ctx, r, "tech", date(2000, 1, 3), END)
    assert r.current() == []


async def test_a_cancelled_backtest_is_retried_with_the_same_request(runner):
    ctx, r = runner
    job = submit_backtest(ctx, r, "tech", START, END, 2.5, 0.75)
    r.cancel(job.id)  # still queued: its worker has not had a turn yet
    again = retry(ctx, r, job.id)
    await r.wait(again.id)
    assert again.subject == job.subject and again.state == "done", again.error
    assert (again.result.backtest.slippage_bps, again.result.backtest.commission) == (2.5, 0.75)


def market(ctx, bars):
    """Stored prices for a listing on no watchlist: only the regime line's mood and ranks read it."""
    s = bars[0].symbol
    UniverseRepo(ctx.conn).add(Listing(s, f"{s} Inc", "Equity", "United States", "NMS", currency="USD",
                                       company_key=s.lower(), is_primary=True))
    BarRepo(ctx.conn).upsert(bars, "2026-09-24T00:00:00Z")


def test_the_regime_line_trades_the_blend_beside_momentum_and_says_which_inputs_it_had(make_ctx):
    ctx = make_ctx({"UP": BARS, "SLOW": rising(400, "SLOW", daily=0.001)})
    market(ctx, rising(400, "MKT", daily=0.002))
    r = run_backtest(ctx, "tech", START, END, commission=2.0)
    assert [x.name for x in r.lines] == ["strategy", "buy_and_hold", "momentum", "regime", "regime_v2"]
    regime = r.lines[3]
    assert regime.label == "Regime" and regime.points[0] == (START, 0.0)
    assert regime.trades >= 1 and regime.costs >= regime.trades * 2.0
    assert "stored market (3 listings: companies only, Tesla and funds left out)" in regime.caption
    assert "no AI verdicts" in regime.caption and "no value or quality" in regime.caption
    assert "no earnings drift" in regime.caption
    assert "every line likely looks better" in " ".join(r.notes) and "The strategy line leaves out" in " ".join(r.notes)


def test_the_regime_v2_line_trades_its_own_book_and_says_what_it_changes(make_ctx):
    ctx = make_ctx({"UP": BARS, "SLOW": rising(400, "SLOW", daily=0.001)})
    market(ctx, rising(400, "MKT", daily=0.002))
    r = run_backtest(ctx, "tech", START, END, commission=2.0)
    v1, v2 = r.lines[3], r.lines[4]
    assert (v2.name, v2.label) == ("regime_v2", "Regime-v2") and v2.points[0] == (START, 0.0)
    assert v2.caption.startswith("Regime-v2: the regime book with two changes.")
    assert "20 trading days or more" in v2.caption and "0.25 or more under the best buy not held" in v2.caption
    assert v2.caption.endswith(v1.caption.removeprefix("Regime: "))
    [note] = [n for n in r.notes if n.startswith("Regime-v2 rotated")]
    assert f"in {v2.trades} trades against the regime line's {v1.trades}" in note


def test_the_regime_line_never_sees_market_bars_after_the_last_day(make_ctx):
    later = rising(460, "MKT", daily=0.002)
    wild = [replace(b, open=b.open / 4, close=b.close / 4, adj_close=b.close / 4) for b in later[400:]]
    runs = []
    for name, extra in (("a.db", []), ("b.db", wild)):
        ctx = make_ctx({"UP": BARS, "SLOW": rising(400, "SLOW", daily=0.001)}, name)
        market(ctx, later[:400] + extra)
        runs.append(run_backtest(ctx, "tech", START, END))
    assert runs[0] == runs[1] and runs[0].lines[3].trades >= 1


def test_the_regime_line_reads_value_and_quality_only_from_the_day_recorded(make_ctx):
    ctx = make_ctx({"UP": BARS})
    as_of, computed = BARS[345].date, BARS[350].date
    SignalRepo(ctx.conn).save([AnalystSignal(a, "UP", as_of, 0.8, 0.7, "stored", ()) for a in ("value", "quality")],
                              f"{computed.isoformat()}T21:00:00Z")
    caption = run_backtest(ctx, "tech", START, END).lines[3].caption
    assert f"stored signals of 1 listing, each only from the day it was recorded (the first on {computed})" in caption
    assert "no value or quality" in run_backtest(ctx, "tech", START, BARS[349].date).lines[3].caption


LONG = rising(700)
WF_START, WF_END = LONG[300].date, LONG[-1].date


def long_ctx(make_ctx, name="o.db"):
    ctx = make_ctx({"UP": LONG, "SLOW": rising(700, "SLOW", daily=0.001)}, name)
    market(ctx, rising(700, "MKT", daily=0.002))
    return ctx


def test_the_walk_forward_judges_every_line_per_six_month_segment_and_fits_regime_wf_at_each_start(make_ctx):
    r = run_backtest(long_ctx(make_ctx), "tech", WF_START, WF_END, walk_forward=True)
    wf = r.walk_forward
    assert wf.months == 6 and wf.segments == tuple(segments(WF_START, WF_END, 6)) and len(wf.segments) == 3
    names = [x.name for x in wf.lines]
    assert names == [x.name for x in r.lines] == ["strategy", "buy_and_hold", "momentum", "regime", "regime_v2",
                                                     "regime_wf"]
    assert all(len(x.segments) == 3 for x in wf.lines)
    held = wf.lines[1]
    assert held.beat is None and all(s.excess == 0.0 for s in held.segments)
    assert all(x.beat == sum(1 for s in x.segments if s.excess > 0) for x in wf.lines if x.name != "buy_and_hold")
    assert len(wf.fits) == 3
    for (day, presets), (a, b) in zip(wf.fits, wf.segments):
        assert a <= day <= b and set(presets) == set(MOODS)
        assert all(sum(row.values()) == pytest.approx(1.0) for row in presets.values())
    wf_line = r.lines[-1]
    assert wf_line.label == "Regime-wf" and "refitted" in wf_line.caption
    assert any("fresh start" in n for n in r.notes)


def test_each_segment_is_a_fresh_start_as_its_own_backtest_would_be(make_ctx):
    ctx = long_ctx(make_ctx)
    wf = run_backtest(ctx, "tech", WF_START, WF_END, walk_forward=True).walk_forward
    a, b = wf.segments[1]
    alone = {x.name: x for x in run_backtest(ctx, "tech", a, b).lines}
    for line in wf.lines:
        if line.name in ("strategy", "buy_and_hold", "momentum"):
            assert line.segments[1].ret == pytest.approx(alone[line.name].return_)
            assert line.segments[1].max_fall == pytest.approx(alone[line.name].max_fall)


def test_no_segment_reads_bars_after_its_end(make_ctx):
    runs = []
    first_end = segments(WF_START, WF_END, 6)[0][1]
    for name in ("a.db", "b.db"):
        ctx = long_ctx(make_ctx, name)
        if name == "b.db":
            wild = [replace(b, open=b.open * 3, close=b.close * 3, adj_close=b.close * 3) for b in LONG
                    if b.date > first_end]
            BarRepo(ctx.conn).upsert(wild, "2026-09-24T00:00:00Z")
        runs.append(run_backtest(ctx, "tech", WF_START, WF_END, walk_forward=True).walk_forward)
    firsts = [[(x.name, x.segments[0]) for x in wf.lines] for wf in runs]
    assert firsts[0] == firsts[1] and runs[0].fits[0] == runs[1].fits[0]
    assert [x.segments[1] for x in runs[0].lines] != [x.segments[1] for x in runs[1].lines]


def test_without_the_walk_forward_there_is_no_regime_wf_line(make_ctx):
    r = run_backtest(long_ctx(make_ctx), "tech", WF_START, WF_END)
    assert r.walk_forward is None and "regime_wf" not in [x.name for x in r.lines]


def test_a_walk_forward_too_short_for_one_segment_is_refused(make_ctx):
    ctx = long_ctx(make_ctx)
    with pytest.raises(InvalidQuery, match="walk-forward"):
        check_backtest(ctx, "tech", WF_START, WF_START + timedelta(days=60), 10.0, 0.0, walk_forward=True)


def test_a_kept_walk_forward_reads_back_whole(make_ctx):
    ctx = long_ctx(make_ctx)
    kept = keep_backtest(ctx, run_backtest(ctx, "tech", WF_START, WF_END, walk_forward=True))
    assert kept_backtest(ctx, kept.id).backtest == kept.backtest


async def test_a_walk_forward_job_is_its_own_request_and_is_retried_as_one(make_ctx):
    ctx = long_ctx(make_ctx)
    r = JobRunner(ctx)
    try:
        job = submit_backtest(ctx, r, "tech", WF_START, WF_END, 2.5, 0.75, walk_forward=True)
        plain = submit_backtest(ctx, r, "tech", WF_START, WF_END, 2.5, 0.75)
        assert job.id != plain.id and "walk-forward" in job.label and "walk-forward" not in plain.label
        r.cancel(job.id)
        r.cancel(plain.id)
        again = retry(ctx, r, job.id)
        assert (again.subject, again.label) == (job.subject, job.label)
        r.cancel(again.id)
        assert retry(ctx, r, plain.id).subject == plain.subject
    finally:
        await r.stop()
