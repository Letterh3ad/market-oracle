from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.analysts.signals import AnalystSignal
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
from oracle.services.regime_tilt import TILTED
from tests.fakes.clock import FakeClock
from tests.fakes.tilt_model import TiltModel

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


def ai_ctx(make_ctx, monkeypatch, model):
    ctx = make_ctx({"UP": BARS, "SLOW": rising(400, "SLOW", daily=0.001)})
    market(ctx, rising(400, "MKT", daily=0.002))
    monkeypatch.setattr("oracle.services.regime_tilt.tilt_model", lambda c: (model, "fake (fake tilt-1)"))
    return ctx


KEPT = {"trend": 1.0, "reason": "kept"}


def test_the_plain_backtest_never_asks_the_model(make_ctx, monkeypatch):
    model = TiltModel(KEPT)
    r = run_backtest(ai_ctx(make_ctx, monkeypatch, model), "tech", START, END)
    assert [x.name for x in r.lines] == ["strategy", "buy_and_hold", "momentum", "regime", "regime_v2"]
    assert model.asked == []


def test_the_regime_ai_line_asks_the_model_for_every_replayed_day_beside_regime(make_ctx, monkeypatch):
    model = TiltModel(KEPT)
    ctx = ai_ctx(make_ctx, monkeypatch, model)
    said = []
    r = run_backtest(ctx, "tech", START, END, commission=2.0, regime_ai=True, progress=said.append)
    plain = run_backtest(ctx, "tech", START, END, commission=2.0)
    assert [x.name for x in r.lines] == ["strategy", "buy_and_hold", "momentum", "regime", "regime_v2", "regime_ai"]
    ai = r.lines[5]
    assert ai.label == "Regime AI" and "fake (fake tilt-1)" in ai.caption and "date left out" in ai.caption
    assert r.lines[:5] == plain.lines, "the other lines are the plain backtest's"
    assert (ai.points, ai.trades) == (r.lines[3].points, r.lines[3].trades), "every tilt x1: it trades like regime"
    decided = [b.date for b in BARS if START <= b.date < END]
    note = next(n for n in r.notes if n.startswith("Regime AI:"))
    assert f"{len(decided)} days replayed" in note, "the last day decides nothing, so it is not asked"
    calls = len(model.asked)
    assert f"{calls} model call" in note and f"{len(decided) - calls} answered from the run's cache" in note
    assert said[-1].startswith(f"Regime AI tilt at {decided[-1].isoformat()}")
    assert all(str(y) not in m["content"] for y in (START.year, END.year) for m in model.asked[0][0]), "date-blind"


def test_a_regime_ai_tilt_against_its_evidence_is_reset_and_counted(make_ctx, monkeypatch):
    model = TiltModel({**dict.fromkeys(TILTED, 1.5), "reason": "raise everything"})
    r = run_backtest(ai_ctx(make_ctx, monkeypatch, model), "tech", START, END, regime_ai=True)
    note = next(n for n in r.notes if n.startswith("Regime AI:"))
    assert "with a move reset to x1 (no sign or record: " in note and model.asked


async def test_a_regime_ai_backtest_job_shows_its_progress_keeps_its_line_and_retries_as_asked(make_ctx, monkeypatch):
    ctx = ai_ctx(make_ctx, monkeypatch, TiltModel(KEPT))
    r = JobRunner(ctx)
    plain = submit_backtest(ctx, r, "tech", START, END)
    job = submit_backtest(ctx, r, "tech", START, END, regime_ai=True)
    assert job is not plain and job.label.endswith(", with regime AI") and job.subject.endswith("|regime_ai")
    assert submit_backtest(ctx, r, "tech", START, END, regime_ai=True) is job
    await r.wait(job.id)
    assert job.state == "done", job.error
    assert [x.name for x in job.result.backtest.lines][-1] == "regime_ai"
    assert [x.name for x in plain.result.backtest.lines][-1] == "regime_v2"
    assert job.progress_fn().startswith("Regime AI tilt at ") and plain.progress_fn is None
    dropped = submit_backtest(ctx, r, "tech", START, END, 2.5, 0.75, regime_ai=True)
    r.cancel(dropped.id)
    again = retry(ctx, r, dropped.id)
    await r.wait(again.id)
    assert again.subject == dropped.subject and again.result.backtest.lines[-1].name == "regime_ai"
    assert (again.result.backtest.slippage_bps, again.result.backtest.commission) == (2.5, 0.75)
    await r.stop()


async def test_a_cancelled_regime_ai_job_stops_asking_the_model(make_ctx, monkeypatch):
    holder = {}

    class Cancelling(TiltModel):
        async def reply(self, messages, schema):
            holder["job"].on_cancel()
            return await super().reply(messages, schema)

    model = Cancelling(KEPT)
    ctx = ai_ctx(make_ctx, monkeypatch, model)
    r = JobRunner(ctx)
    holder["job"] = job = submit_backtest(ctx, r, "tech", START, END, regime_ai=True)
    await r.wait(job.id)
    assert len(model.asked) == 1, "the flag a Cancel sets ends the asking"
    await r.stop()


def test_a_stopped_regime_ai_replay_asks_no_more_and_still_finishes(make_ctx, monkeypatch):
    model = TiltModel(KEPT)
    r = run_backtest(ai_ctx(make_ctx, monkeypatch, model), "tech", START, END, regime_ai=True, stop=lambda: True)
    assert model.asked == [] and r.lines[5].points and "0 model calls" in " ".join(r.notes)
