import json
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.config import Config
from oracle.market.bars import Bar
from oracle.research.trace import Report, RunStatus, Verdict
from oracle.services import replay_score
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.prices_service import ListingOutcome
from oracle.services.replay_score import LIMITS, replay_report, score_study, update_study_prices, wilson
from oracle.services.replay_service import study_status
from oracle.store.bars import BarRepo
from oracle.store.replay import ReplayRepo
from oracle.store.research import ResearchRepo
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
JUN, JUL, AUG = date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 3)
LISTINGS = [{"symbol": s, "name": s, "region": "US", "sector": "Energy", "market_cap": "Mega Cap"}
            for s in ("AAA", "BBB", "CCC")]
# 1m targets: Jun -> Jul 1, Jul -> Aug 3 (Aug 1 is a Saturday), Aug -> Sep 3.
# 3m targets: Jun -> Sep 1; Jul and Aug fall after today, so pending.
PRICES = {  # entries are the first closes after each as-of day: Jun 2, Jul 2, Aug 4
    "AAA": {"2026-06-02": 100, "2026-07-01": 110, "2026-07-02": 110, "2026-08-03": 99, "2026-08-04": 99,
            "2026-09-01": 120, "2026-09-03": 90, "2026-09-23": 100},
    "BBB": {"2026-06-02": 50, "2026-07-01": 50, "2026-07-02": 50, "2026-08-03": 55, "2026-08-04": 55,
            "2026-09-01": 45, "2026-09-03": 55, "2026-09-23": 55},
}
# Basket 1m: Jun +5% (AAA +10%, BBB 0), Jul 0 (AAA -10%, BBB +10%). Basket 3m: Jun +5% (+20%, -10%).
# CCC has no bars at all, so it is left out of the basket.


def bars(symbol, closes, adj=None):
    return [Bar(symbol, date.fromisoformat(d), None, None, None, c, (adj or {}).get(d), None, "USD", "test")
            for d, c in closes.items()]


def replay_run(ctx, symbol, day, label=None, confidence=0.0, rejected=None):
    repo = ResearchRepo(ctx.conn)
    run = repo.start(symbol, "ollama", "m", "2026-09-20T12:00:00Z", as_of=day)
    call = Verdict(label, confidence, "why", (), "if not") if label else None
    repo.save_report(run, symbol, Report(day, (), None, None, (), None, verdict=call,
                                         verdict_rejected=None if label else (rejected or "no evidence")))
    repo.finish(run, RunStatus.DONE, "notes", None, "2026-09-20T12:05:00Z")
    return run


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW)) as c:
        for symbol, closes in PRICES.items():
            BarRepo(c.conn).upsert(bars(symbol, closes), "2026-09-23T22:00:00Z")
        yield c


@pytest.fixture
def study(ctx):
    """AAA and BBB replayed; CCC's replays not run yet."""
    repo = ReplayRepo(ctx.conn)
    sid = repo.create(NOW, JUN, AUG, json.dumps(LISTINGS),
                      [(p["symbol"], d) for d in (JUN, JUL, AUG) for p in LISTINGS])
    cells = {(c.symbol, c.as_of): c.id for c in repo.cells(sid)}
    at = datetime(2026, 9, 20, 12, 5, tzinfo=timezone.utc)
    for key, verdict in {("AAA", JUN): ("buy", 0.9), ("BBB", JUN): ("sell", 0.7), ("AAA", JUL): ("buy", 0.85),
                         ("BBB", JUL): ("hold", 0.5)}.items():
        repo.mark_done(cells[key], replay_run(ctx, *key, *verdict), at)
    repo.mark_done(cells[("AAA", AUG)], replay_run(ctx, "AAA", AUG), at)
    repo.mark_failed(cells[("BBB", AUG)], "model down", at, requeue=False)
    return sid


def horizon(score, name):
    return next(h for h in score.horizons if h.horizon == name)


def group(groups, name):
    return next(g for g in groups if g.name == name)


def test_each_verdict_gets_its_forward_returns_and_the_baskets(ctx, study):
    cells = {(c.symbol, c.as_of): c for c in replay_report(ctx).cells}
    aaa = cells[("AAA", JUN)]
    assert (aaa.verdict, aaa.confidence) == ("buy", 0.9)
    one, three = aaa.outcomes
    assert (one.horizon, one.state) == ("1m", "scored")
    assert one.ret == pytest.approx(0.10) and one.basket == pytest.approx(0.05) and one.vs_basket == pytest.approx(0.05)
    assert three.ret == pytest.approx(0.20) and three.vs_basket == pytest.approx(0.15)
    jul = cells[("AAA", JUL)].outcomes
    assert jul[0].ret == pytest.approx(-0.10)  # to Aug 3, the first weekday after Aug 1
    assert (jul[1].state, jul[1].ret) == ("pending", None)  # Oct 1 is still ahead: pending, not zero
    assert cells[("AAA", AUG)].verdict is None and cells[("BBB", AUG)].state == "failed"


def test_hit_rates_and_mean_returns_by_verdict_absolute_and_against_the_basket(ctx, study):
    one = horizon(replay_report(ctx), "1m")
    assert (one.scored, one.pending, one.unpriced) == (4, 0, 0)
    # Buys and sells only: AAA Jun up (hit), BBB Jun flat (a sell needs a fall), AAA Jul down.
    assert one.hit_rate == pytest.approx(1 / 3) and one.hit_rate_vs_basket == pytest.approx(2 / 3)
    buy, hold, sell = (group(one.by_verdict, v) for v in ("buy", "hold", "sell"))
    assert (buy.n, buy.hit_rate, buy.hit_rate_vs_basket) == (2, 0.5, 0.5)
    assert buy.mean_return == pytest.approx(0.0) and buy.mean_vs_basket == pytest.approx(-0.025)
    assert buy.mean_confidence == pytest.approx(0.875)
    assert (sell.n, sell.hit_rate, sell.hit_rate_vs_basket) == (1, 0.0, 1.0)
    assert (hold.n, hold.hit_rate) == (1, None) and hold.mean_return == pytest.approx(0.10)
    three = horizon(replay_report(ctx), "3m")
    assert (three.scored, three.pending, three.hit_rate, three.hit_rate_vs_basket) == (2, 2, 1.0, 1.0)


def test_confidence_bands_score_buys_and_sells_as_called(ctx, study):
    one = horizon(replay_report(ctx), "1m")
    assert [g.name for g in one.by_band] == ["under 0.60", "0.60 to 0.80", "0.80 and up"]
    low, mid, high = one.by_band
    assert (low.n, low.hit_rate, low.mean_return) == (0, None, None)  # the hold is not a call
    assert (high.n, high.hit_rate) == (2, 0.5) and high.mean_return == pytest.approx(0.0)
    # A sell's return counts with its sign flipped: BBB lagged the basket by 5%, so +5% as called.
    assert (mid.n, mid.hit_rate_vs_basket) == (1, 1.0) and mid.mean_vs_basket == pytest.approx(0.05)


def test_verdict_mix_counts_and_limits(ctx, study):
    s = replay_report(ctx)
    assert (s.total, s.verdicts, s.no_verdict, s.failed, s.to_run) == (9, 4, 1, 1, 3)
    assert (s.mix.buy, s.mix.hold, s.mix.sell, s.mix.buy_share) == (2, 1, 1, 0.5)
    assert s.mix.mean_confidence == pytest.approx(0.7375)
    assert s.limits == LIMITS and any("survivorship" in line for line in s.limits)
    assert any("GDELT" in line for line in s.limits) and any("interim" in line.lower() for line in s.limits)


def test_a_window_that_closed_without_bars_is_unpriced_and_names_the_listing(ctx, study):
    ctx.conn.execute("DELETE FROM bars WHERE symbol = 'AAA' AND date >= '2026-09-01'")
    s = replay_report(ctx)
    three = horizon(s, "3m")
    assert (three.scored, three.unpriced) == (1, 1)
    assert s.unpriced_listings == ("AAA", "CCC")


def test_adjusted_closes_are_used_when_both_ends_have_one(ctx, study):
    BarRepo(ctx.conn).upsert(bars("AAA", {"2026-06-02": 100, "2026-07-01": 110},
                                  adj={"2026-06-02": 95, "2026-07-01": 110}), "2026-09-23T22:00:00Z")
    aaa = next(c for c in replay_report(ctx).cells if (c.symbol, c.as_of) == ("AAA", JUN))
    assert aaa.outcomes[0].ret == pytest.approx(110 / 95 - 1)


def test_base_rates_beside_the_hit_rates(ctx, study):
    s = replay_report(ctx)
    one, three = horizon(s, "1m"), horizon(s, "3m")
    # 1m windows of AAA and BBB: up in Jun (AAA) and Jul (BBB); beat the basket Jun AAA, Jul BBB, Aug BBB.
    assert one.base_rate_up == pytest.approx(2 / 6) and one.base_rate_beat_basket == pytest.approx(3 / 6)
    assert (three.base_rate_up, three.base_rate_beat_basket) == (0.5, 0.5)


def test_a_basket_under_half_the_listings_leaves_relative_results_unpriced(ctx, study):
    ctx.conn.execute("DELETE FROM bars WHERE symbol = 'BBB'")
    s = replay_report(ctx)
    aaa = next(c for c in s.cells if (c.symbol, c.as_of) == ("AAA", JUN)).outcomes[0]
    assert aaa.ret == pytest.approx(0.10) and (aaa.basket, aaa.basket_n, aaa.vs_basket) == (None, 1, None)
    one = horizon(s, "1m")
    assert one.hit_rate == pytest.approx(0.5) and one.hit_rate_vs_basket is None


def test_the_wilson_interval_and_none_under_ten():
    lo, hi = wilson(5, 10)
    assert (lo, hi) == (pytest.approx(0.2366, abs=1e-4), pytest.approx(0.7634, abs=1e-4))
    assert wilson(3, 9) is None and wilson(10, 10)[1] == 1.0


def test_the_status_carries_a_small_interim_score(ctx, study):
    score = study_status(ctx).to_json()["score"]
    assert set(score) == {"verdicts", "no_verdict", "buy_share", "mean_confidence", "prices_updated_at", "horizons",
                          "note"}
    assert (score["verdicts"], score["no_verdict"], score["buy_share"]) == (4, 1, 0.5)
    assert score["prices_updated_at"] is None and "limits" in score["note"]
    one = score["horizons"]["1m"]
    assert set(one) == {"scored", "pending", "unpriced", "hit_rate", "hit_rate_ci", "hit_rate_vs_basket",
                        "hit_rate_vs_basket_ci", "base_rate_up", "base_rate_beat_basket", "by_verdict"}
    assert (one["scored"], one["pending"], one["hit_rate_ci"]) == (4, 0, None)  # 3 calls: too few for an interval
    assert one["by_verdict"]["sell"] == {"n": 1, "mean_return": 0.0, "mean_as_called": 0.0}
    hold = one["by_verdict"]["hold"]
    assert hold["n"] == 1 and hold["mean_return"] == pytest.approx(0.10) and hold["mean_as_called"] is None
    assert score["horizons"]["3m"]["pending"] == 2


def test_scoring_never_fetches(ctx, study, monkeypatch):
    async def boom(*_):
        raise AssertionError("fetched")

    monkeypatch.setattr(replay_score, "update_listings", boom)
    assert score_study(ctx, ReplayRepo(ctx.conn).current()) is not None
    study_status(ctx)


async def test_update_refetches_whole_windows_of_listings_whose_bars_stop_short(ctx, study, monkeypatch):
    asked = []

    async def update(c, symbols, since):
        asked.append((list(symbols), since))
        BarRepo(c.conn).upsert(bars("CCC", {"2026-06-02": 10, "2026-07-01": 11, "2026-07-02": 11, "2026-08-03": 12,
                                            "2026-08-04": 12, "2026-09-03": 12}), "2026-09-24T12:00:00Z")
        return [ListingOutcome("AAA", 0, None, "no price data for AAA"), ListingOutcome("CCC", 6, "yahoo", None)]

    monkeypatch.setattr(replay_score, "update_listings", update)
    ctx.conn.execute("DELETE FROM bars WHERE symbol = 'AAA' AND date >= '2026-09-01'")
    outcomes = await update_study_prices(ctx)
    assert asked == [(["AAA", "CCC"], date(2026, 5, 25))]  # the first as-of day less a week
    assert [(o.symbol, o.error) for o in outcomes] == [("AAA", "no price data for AAA"), ("CCC", None)]
    s = replay_report(ctx)
    assert s.unpriced_listings == ("AAA",) and s.prices_updated_at == NOW
    # CCC now joins the June basket: (+10% + 0 + 10%) / 3.
    aaa = next(c for c in s.cells if (c.symbol, c.as_of) == ("AAA", JUN))
    assert aaa.outcomes[0].basket == pytest.approx(0.2 / 3) and aaa.outcomes[0].basket_n == 3


async def test_no_study_no_report(ctx):
    assert replay_report(ctx) is None
    with pytest.raises(InvalidQuery):
        await update_study_prices(ctx)


# A dense series: every weekday has a distinct close, so a return names the two days it spans.
DENSE_FROM, DENSE_TO = date(2024, 12, 2), NOW.date()


def close(day):
    return 1000.0 + (day - DENSE_FROM).days


def ret(entry, exit_):
    return close(date.fromisoformat(exit_)) / close(date.fromisoformat(entry)) - 1


@pytest.fixture
def dense(ctx):
    days = [DENSE_FROM + timedelta(n) for n in range((DENSE_TO - DENSE_FROM).days + 1)]
    BarRepo(ctx.conn).upsert(bars("DDD", {d.isoformat(): close(d) for d in days if d.weekday() < 5}), "t")
    listing = [{"symbol": "DDD", "name": "D", "region": "US", "sector": "Energy", "market_cap": "Mega Cap"}]
    as_of = [date(2025, 1, 31), date(2026, 6, 1), date(2026, 6, 3), date(2026, 8, 24)]
    ReplayRepo(ctx.conn).create(NOW, as_of[0], as_of[-1], json.dumps(listing), [("DDD", d) for d in as_of])

    def outcomes(day):
        return next(c for c in replay_report(ctx).cells if c.as_of == day).outcomes

    return outcomes


def test_entry_is_the_first_close_after_the_as_of_day_and_exit_the_close_at_the_target(dense):
    one, three = dense(date(2026, 6, 1))
    assert one.ret == pytest.approx(ret("2026-06-02", "2026-07-01"))  # the as-of day's own close is not used
    assert three.ret == pytest.approx(ret("2026-06-02", "2026-09-01"))


def test_a_target_without_a_bar_exits_on_the_last_close_before_it(ctx, dense):
    ctx.conn.execute("DELETE FROM bars WHERE symbol = 'DDD' AND date IN ('2026-07-01', '2026-07-03')")
    assert dense(date(2026, 6, 1))[0].ret == pytest.approx(ret("2026-06-02", "2026-06-30"))  # not Jul 2
    assert dense(date(2026, 6, 3))[0].ret == pytest.approx(ret("2026-06-04", "2026-07-02"))  # a holiday target


def test_a_month_after_the_31st_is_the_months_last_day(dense):
    assert dense(date(2025, 1, 31))[0].ret == pytest.approx(ret("2025-02-03", "2025-02-28"))


def test_an_adjusted_close_at_one_end_only_falls_back_to_plain_closes(ctx, dense):
    BarRepo(ctx.conn).upsert(bars("DDD", {"2026-06-04": close(date(2026, 6, 4))}, adj={"2026-06-04": 1.0}), "t")
    assert dense(date(2026, 6, 3))[0].ret == pytest.approx(ret("2026-06-04", "2026-07-03"))


def test_a_target_of_today_is_pending_even_with_todays_bar(dense):
    one, three = dense(date(2026, 8, 24))
    assert (one.state, one.ret, three.state) == ("pending", None, "pending")


def test_an_exit_close_over_a_week_before_its_target_is_not_a_price(ctx, dense):
    ctx.conn.execute("DELETE FROM bars WHERE symbol = 'DDD' AND date BETWEEN '2025-02-19' AND '2025-02-28'")
    assert dense(date(2025, 1, 31))[0].state == "unpriced"


def test_an_entry_close_over_a_week_after_the_as_of_day_is_not_a_price(ctx, dense):
    ctx.conn.execute("DELETE FROM bars WHERE symbol = 'DDD' AND date BETWEEN '2026-06-02' AND '2026-06-10'")
    assert [o.state for o in dense(date(2026, 6, 1))] == ["unpriced", "unpriced"]
    assert "DDD" not in replay_report(ctx).unpriced_listings  # a refetch would not fill a gap


def test_a_move_like_an_unrebased_split_is_not_a_price(ctx, dense):
    ctx.conn.execute("UPDATE bars SET close = close / 10 WHERE symbol = 'DDD' AND date >= '2026-06-15'")
    one, three = dense(date(2026, 6, 1))
    assert (one.state, three.state) == ("unpriced", "unpriced")
    assert "DDD" not in replay_report(ctx).unpriced_listings

