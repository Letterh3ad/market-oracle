from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.config import Config, StrategyConfig
from oracle.services.backtest_service import run_backtest
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, NoPrices, UnknownWatchlist
from oracle.services.paper_service import place_buy
from oracle.services.watch_service import watch
from oracle.store.bars import BarRepo
from oracle.store.meta import MetaRepo
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
    strategy, held = r.lines
    assert (strategy.name, held.name) == ("strategy", "buy_and_hold")
    assert strategy.trades == len(r.trades) >= 1 and r.trades[0].symbol == "UP"
    assert strategy.points[0] == (START, 0.0) and held.points[0] == (START, 0.0)
    assert held.return_ > strategy.return_ > 0
    assert strategy.costs == pytest.approx(sum(t.costs for t in r.trades))
    text = " ".join(r.notes)
    assert "survivorship" in text and "only today's" in text and "10 basis points" in text


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
        run_backtest(ctx, "tech", date(2020, 1, 1), date(2020, 6, 1))


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
