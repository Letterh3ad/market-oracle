import json
from datetime import date, datetime, timezone

import pytest

from oracle.config import Config
from oracle.market.bars import Bar
from oracle.research.trace import Report, RunStatus, Verdict
from oracle.services import replay_score
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, PriceUnavailable
from oracle.services.prices_service import PriceUpdate
from oracle.services.replay_score import LIMITS, replay_report, score_study, update_study_prices
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
PRICES = {
    "AAA": {"2026-06-01": 100, "2026-07-01": 110, "2026-08-03": 99, "2026-09-01": 120, "2026-09-03": 90,
            "2026-09-23": 100},
    "BBB": {"2026-06-01": 50, "2026-07-01": 50, "2026-08-03": 55, "2026-09-01": 45, "2026-09-03": 55,
            "2026-09-23": 55},
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
    BarRepo(ctx.conn).upsert(bars("AAA", {"2026-06-01": 100, "2026-07-01": 110},
                                  adj={"2026-06-01": 95, "2026-07-01": 110}), "2026-09-23T22:00:00Z")
    aaa = next(c for c in replay_report(ctx).cells if (c.symbol, c.as_of) == ("AAA", JUN))
    assert aaa.outcomes[0].ret == pytest.approx(110 / 95 - 1)


def test_an_entry_close_older_than_a_week_is_not_a_price(ctx, study):
    ctx.conn.execute("DELETE FROM bars WHERE symbol = 'AAA' AND date = '2026-06-01'")
    BarRepo(ctx.conn).upsert(bars("AAA", {"2026-05-20": 100}), "2026-09-23T22:00:00Z")
    aaa = next(c for c in replay_report(ctx).cells if (c.symbol, c.as_of) == ("AAA", JUN))
    assert [o.state for o in aaa.outcomes] == ["unpriced", "unpriced"]


def test_the_status_carries_a_small_interim_score(ctx, study):
    score = study_status(ctx).to_json()["score"]
    assert set(score) == {"verdicts", "no_verdict", "buy_share", "mean_confidence", "horizons"}
    assert (score["verdicts"], score["no_verdict"], score["buy_share"]) == (4, 1, 0.5)
    one = score["horizons"]["1m"]
    assert set(one) == {"scored", "pending", "unpriced", "hit_rate", "hit_rate_vs_basket", "mean_return"}
    assert (one["scored"], one["pending"]) == (4, 0)
    assert one["mean_return"]["hold"] == pytest.approx(0.10) and set(one["mean_return"]) == {"buy", "hold", "sell"}
    assert score["horizons"]["3m"]["pending"] == 2


def test_scoring_never_fetches(ctx, study, monkeypatch):
    async def boom(*_):
        raise AssertionError("fetched")

    monkeypatch.setattr(replay_score, "update_listing", boom)
    assert score_study(ctx, ReplayRepo(ctx.conn).current()) is not None
    study_status(ctx)


async def test_update_fetches_only_listings_with_a_closed_window_lacking_bars(ctx, study, monkeypatch):
    asked = []

    async def update(c, symbol):
        asked.append(symbol)
        if symbol == "AAA":
            raise PriceUnavailable("no price data for AAA")
        BarRepo(c.conn).upsert(bars(symbol, {"2026-06-01": 10, "2026-07-01": 11, "2026-08-03": 12,
                                             "2026-09-03": 12}), "2026-09-24T12:00:00Z")
        return PriceUpdate(symbol, 4, date(2026, 9, 3), "yahoo")

    monkeypatch.setattr(replay_score, "update_listing", update)
    ctx.conn.execute("DELETE FROM bars WHERE symbol = 'AAA' AND date >= '2026-09-01'")
    outcomes = await update_study_prices(ctx)
    assert asked == ["AAA", "CCC"]
    assert [(o.symbol, o.added, o.error) for o in outcomes] == [
        ("AAA", 0, "no price data for AAA"), ("CCC", 4, None)]
    assert replay_report(ctx).unpriced_listings == ("AAA",)
    # CCC now joins the June basket: (+10% + 0 + 10%) / 3.
    aaa = next(c for c in replay_report(ctx).cells if (c.symbol, c.as_of) == ("AAA", JUN))
    assert aaa.outcomes[0].basket == pytest.approx(0.2 / 3)


async def test_no_study_no_report(ctx):
    assert replay_report(ctx) is None
    with pytest.raises(InvalidQuery):
        await update_study_prices(ctx)
