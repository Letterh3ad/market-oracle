import json
from datetime import date, datetime, time, timedelta, timezone

from oracle.analysts.signals import AnalystSignal
from oracle.broker.orders import Side
from oracle.services.backtest_service import run_backtest
from oracle.services.paper_service import strategy_rules
from oracle.services.regime_lines import combined, market_history, regime_line
from oracle.services.regime_service import regime_step
from oracle.store.bars import BarRepo
from oracle.store.meta import MetaRepo
from oracle.store.paper import FillRow, PaperRepo
from oracle.store.regime import RegimeStateRepo
from oracle.store.signals import SignalRepo
from oracle.store.universe import UniverseRepo
from oracle.store.watchlists import WatchlistRepo
from oracle.universe.listings import Listing
from tests.broker.test_backtest import rising
from tests.fakes.clock import FakeClock
from tests.services.test_backtest import make_ctx  # noqa: F401 (fixture)

BARS = rising(400)
START, END = BARS[300].date, BARS[-1].date


def test_a_signal_counts_from_the_later_of_its_as_of_day_and_the_day_it_was_computed(make_ctx):
    ctx = make_ctx({"UP": BARS})
    repo = SignalRepo(ctx.conn)
    repo.save([AnalystSignal("value", "UP", date(2026, 9, 1), 0.4, 0.5, "late", ())], "2026-09-03T21:00:00Z")
    repo.save([AnalystSignal("value", "UP", date(2026, 9, 5), -0.2, 0.5, "same day", ())], "2026-09-05T21:00:00Z")
    assert repo.recorded("UP", "value") == [(date(2026, 9, 3), 0.4), (date(2026, 9, 5), -0.2)]


def test_value_and_quality_combine_as_the_mean_of_each_ones_newest_score():
    d1, d2, d3 = date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)
    assert combined({"value": [(d1, 0.4), (d3, 0.0)], "quality": [(d2, -0.2)]}) == (
        (d1, 0.4), (d2, 0.1), (d3, -0.1))
    assert combined({"value": [], "quality": []}) == ()


def listed(ctx, symbol, name, kind="Equity"):
    UniverseRepo(ctx.conn).add(Listing(symbol, name, kind, "United States", "NMS", currency="USD",
                                       company_key=symbol.lower(), is_primary=True))
    BarRepo(ctx.conn).upsert(rising(420, symbol), "2026-09-24T00:00:00Z")


def stored_only(ctx, symbol="MAP", daily=-0.004):
    """A company with stored bars on no watchlist and held by no account (a backfilled map company)."""
    listed(ctx, symbol, f"{symbol} Inc")
    BarRepo(ctx.conn).upsert(rising(420, symbol, daily=daily), "2026-09-24T00:00:00Z")


def test_the_market_is_the_watched_or_held_companies_up_to_the_last_day_without_tesla_or_funds(make_ctx):
    ctx = make_ctx({"UP": BARS})
    listed(ctx, "OTHER", "Other Inc")
    listed(ctx, "HELD", "Held Inc")
    listed(ctx, "TSLA", "Tesla, Inc.")
    listed(ctx, "VUSA.L", "Vanguard S&P 500 UCITS ETF", "ETF")
    stored_only(ctx)
    BarRepo(ctx.conn).upsert(rising(420, "UNLISTED"), "2026-09-24T00:00:00Z")
    WatchlistRepo(ctx.conn).create("other", "2026-09-24T00:00:00Z")
    for s in ("OTHER", "TSLA", "VUSA.L", "UNLISTED"):
        WatchlistRepo(ctx.conn).add("other", s, "2026-09-24T00:00:00Z")
    paper = PaperRepo(ctx.conn)
    order = paper.add_order("manual", "HELD", Side.BUY, None, 1, "2026-09-01T14:00:00Z", date(2026, 9, 1), "manual")
    paper.record_fill(FillRow(order.id, "manual", "HELD", Side.BUY, date(2026, 9, 1), 1, 100.0, "USD", 1.0,
                              100.0, 0.0, 0.0, 0.0, 0.0))
    got = market_history(ctx, START, END, ["UP"])
    assert set(got) == {"UP", "OTHER", "HELD"}
    assert all(r[0].bar.date == START and r[-1].bar.date == END for r in got.values())
    assert set(market_history(ctx, START, END, ["UP", "VUSA.L", "TSLA"])) == {"UP", "OTHER", "HELD", "VUSA.L"}
    line = regime_line(ctx, START, END, strategy_rules(ctx), ["UP"])
    assert "(3 listings: companies on a watchlist or held, Tesla and funds left out)" in line.caption
    assert "today's watched and held listings back in time" in line.caption


def regime_runs(ctx):
    runs = {}
    for account in ("regime", "regime-v2", "core-sat"):
        run = regime_step(ctx, account)
        runs[account] = (run.day, run.mood, run.flags, [(d.symbol, d.action, d.reason) for d in run.decisions],
                         RegimeStateRepo(ctx.conn).latest(account).state)
    return runs


def test_bars_stored_for_an_untraded_listing_change_no_score_mood_flag_or_lean_live_or_replayed(make_ctx):
    runs = []
    for name, extra in (("a.db", False), ("b.db", True)):
        ctx = make_ctx({"UP": BARS, "SLOW": rising(400, "SLOW", daily=0.001)}, name)
        listed(ctx, "MKT", "Market Inc")
        WatchlistRepo(ctx.conn).create("main", "2026-09-24T00:00:00Z")
        for s in ("UP", "SLOW", "MKT"):
            WatchlistRepo(ctx.conn).add("main", s, "2026-09-24T00:00:00Z")
        if extra:
            stored_only(ctx)
        ctx.clock = FakeClock(datetime.combine(END + timedelta(days=1), time(21), timezone.utc))
        replayed = run_backtest(ctx, "tech", START, END, walk_forward=True)
        runs.append((replayed, regime_runs(ctx)))
    assert runs[0] == runs[1]
    replayed, live = runs[0]
    assert replayed.lines[3].trades >= 1 and live["regime"][1] is not None and live["regime"][3]


def test_earnings_days_come_from_stored_releases_up_to_the_last_day(make_ctx):
    ctx = make_ctx({"UP": BARS})
    releases = [[BARS[320].date.isoformat(), True], [BARS[-1].date.isoformat(), None]]
    MetaRepo(ctx.conn).set("earnings_dates:UP", json.dumps({"releases": releases, "fetched_at": "2026-09-24T00:00:00Z"}))
    line = regime_line(ctx, START, BARS[330].date, strategy_rules(ctx), ["UP"])
    assert "earnings drift from the stored SEC release days of 1 listing (none fetched)" in line.caption
    assert "no earnings drift" in regime_line(ctx, START, BARS[310].date, strategy_rules(ctx), ["UP"]).caption


def test_a_start_within_a_year_of_the_first_prices_says_the_momentum_rank_is_missing(make_ctx):
    ctx = make_ctx({"UP": BARS})
    early = regime_line(ctx, BARS[260].date, END, strategy_rules(ctx), ["UP"]).caption
    assert "before any 12-1 momentum rank exists" in early
    assert "momentum rank exists" not in regime_line(ctx, START, END, strategy_rules(ctx), ["UP"]).caption
