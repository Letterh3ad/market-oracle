import json
from datetime import date

from oracle.analysts.signals import AnalystSignal
from oracle.services.paper_service import strategy_rules
from oracle.services.regime_lines import combined, regime_line
from oracle.store.bars import BarRepo
from oracle.store.meta import MetaRepo
from oracle.store.signals import SignalRepo
from tests.broker.test_backtest import rising
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


def test_the_market_is_every_stored_listing_up_to_the_last_day_only(make_ctx):
    ctx = make_ctx({"UP": BARS})
    BarRepo(ctx.conn).upsert(rising(420, "OTHER"), "2026-09-24T00:00:00Z")
    got = BarRepo(ctx.conn).between_all(START, END)
    assert set(got) == {"UP", "OTHER"} and all(b[0].date == START and b[-1].date == END for b in got.values())
    line = regime_line(ctx, START, END, strategy_rules(ctx))
    assert "(2 listings)" in line.caption


def test_earnings_days_come_from_stored_releases_up_to_the_last_day(make_ctx):
    ctx = make_ctx({"UP": BARS})
    releases = [[BARS[320].date.isoformat(), True], [BARS[-1].date.isoformat(), None]]
    MetaRepo(ctx.conn).set("earnings_dates:UP", json.dumps({"releases": releases, "fetched_at": "2026-09-24T00:00:00Z"}))
    line = regime_line(ctx, START, BARS[330].date, strategy_rules(ctx))
    assert "earnings drift from the stored SEC release days of 1 listing (none fetched)" in line.caption
    assert "no earnings drift" in regime_line(ctx, START, BARS[310].date, strategy_rules(ctx)).caption
