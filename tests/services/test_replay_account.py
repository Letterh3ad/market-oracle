import json
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.broker.rules import Lean
from oracle.config import Config, RiskConfig, StrategyConfig
from oracle.market.bars import Bar
from oracle.services import replay_account
from oracle.services.context import open_context
from oracle.services.replay_score import replay_report, score_study
from oracle.store.bars import BarRepo
from oracle.store.replay import ReplayRepo
from tests.fakes.clock import FakeClock
from tests.services.test_replay_score import replay_run

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
JUN, JUL, AUG = date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 3)
LAST = date(2026, 9, 23)
SYMBOLS = ("AAA", "BBB")
# Half the account per position and no cash floor, so buying both is buying the basket; a stop far
# away, so only verdicts sell.
CONFIG = dict(risk=RiskConfig(account=10_000.0, max_position=0.5, cash_floor=0.0, target_volatility=0.2),
              strategy=StrategyConfig(min_probability=0.6, max_positions=10, stop_loss=0.5))


def weekdays(start, end):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def path(moves):
    """A close each weekday from a year before the study, flat with a small wobble (low
    volatility, so each gets the full position) until the study, then `moves(day)` a day."""
    out, close = {}, 100.0
    for i, day in enumerate(weekdays(date(2025, 5, 1), LAST)):
        close *= 1 + (moves(day) if day > JUN else 0.002 * (-1) ** i)
        out[day] = close
    return out


def rise(day):
    return 0.002


def fall_after_july(day):
    return 0.004 if day <= JUL + timedelta(days=1) else -0.006


def store(ctx, symbol, closes):
    BarRepo(ctx.conn).upsert([Bar(symbol, d, None, None, None, c, None, None, "USD", "test") for d, c in closes.items()],
                             "2026-09-23T22:00:00Z")


def make_study(ctx, calls):
    repo = ReplayRepo(ctx.conn)
    listings = [{"symbol": s, "name": s, "region": "US", "sector": "Energy", "market_cap": "Mega Cap"} for s in SYMBOLS]
    sid = repo.create(NOW, JUN, AUG, json.dumps(listings), [(s, d) for d in (JUN, JUL, AUG) for s in SYMBOLS],
                      allowed=1)
    cells = {(c.symbol, c.as_of): c.id for c in repo.cells(sid)}
    at = datetime(2026, 9, 20, 12, 5, tzinfo=timezone.utc)
    for key, verdict in calls.items():
        repo.mark_done(cells[key], replay_run(ctx, *key, *verdict), at)
    return sid


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db", **CONFIG), None, FakeClock(NOW)) as c:
        store(c, "AAA", path(fall_after_july))
        store(c, "BBB", path(rise))
        yield c


def lines(account):
    return {line.name: line for line in account.lines}


def all_buy():
    return {(s, d): ("buy", 0.9) for s in SYMBOLS for d in (JUN, JUL, AUG)}


def test_buying_every_listing_is_buy_and_hold_less_costs(ctx):
    make_study(ctx, all_buy())
    account = replay_report(ctx).account
    ai, held = lines(account)["ai"], lines(account)["buy_and_hold"]
    assert account.listings == SYMBOLS and account.left_out == ()
    assert [(t.symbol, t.day) for t in account.trades] == [("AAA", date(2026, 6, 2)), ("BBB", date(2026, 6, 2))]
    assert ai.trades == 2 and held.trades == 0
    assert ai.return_ < held.return_
    assert ai.return_ == pytest.approx(held.return_, abs=0.004)  # 10 bps slippage and whole shares
    assert ai.max_fall == pytest.approx(held.max_fall, abs=0.004)
    assert account.start == JUN and account.end == LAST


def test_a_correct_sell_beats_buy_and_hold(ctx):
    make_study(ctx, {**all_buy(), ("AAA", JUL): ("sell", 0.8)})
    account = replay_report(ctx).account
    ai, held = lines(account)["ai"], lines(account)["buy_and_hold"]
    sell = [t for t in account.trades if t.side.value == "sell"]
    # Decided on the as-of day's news, filled at the first close after it.
    assert [(t.symbol, t.day) for t in sell] == [("AAA", date(2026, 7, 2))]
    assert ai.return_ > held.return_ + 0.05 and ai.max_fall > held.max_fall
    assert ai.trades == 4 and (account.trades[-1].symbol, account.trades[-1].day) == ("AAA", date(2026, 8, 4))


def test_holds_keep_positions_sells_of_unheld_listings_do_nothing_and_old_verdicts_are_not_bought(ctx):
    make_study(ctx, {("AAA", JUN): ("buy", 0.9), ("BBB", JUN): ("sell", 0.9), ("AAA", JUL): ("hold", 0.7),
                     ("BBB", AUG): ("buy", 0.4)})
    account = replay_report(ctx).account
    assert [(t.symbol, t.side.value) for t in account.trades] == [("AAA", "buy")]


def test_a_listing_the_stored_bars_cannot_price_is_left_out_of_both_lines(ctx):
    closes = path(rise)
    store(ctx, "BBB", {d: c * (0.4 if d >= date(2026, 7, 15) else 1) for d, c in closes.items()})
    make_study(ctx, all_buy())
    account = replay_report(ctx).account
    assert account.listings == ("AAA",)
    assert account.left_out == ("BBB: a day-over-day move outside 0.5x to 2x, taken for a split the stored bars "
                                "were not re-based for",)
    assert "1 listing" in lines(account)["buy_and_hold"].caption
    assert {t.symbol for t in account.trades} == {"AAA"}


def test_without_verdicts_the_account_stays_in_cash(ctx):
    make_study(ctx, {})
    ai = lines(replay_report(ctx).account)["ai"]
    assert ai.trades == 0 and ai.return_ == 0.0


def test_the_status_score_carries_no_account_and_the_json_report_carries_returns(ctx):
    make_study(ctx, all_buy())
    assert score_study(ctx, ReplayRepo(ctx.conn).current()).account is None
    out = replay_report(ctx).to_json()["account"]
    assert [line["name"] for line in out["lines"]] == ["ai", "buy_and_hold"]
    assert out["lines"][0]["return"] == pytest.approx(lines(replay_report(ctx).account)["ai"].return_)
    assert out["lines"][0]["points"][0] == ["2026-06-01", 0.0]


def test_a_baseline_account_runs_through_the_same_engine(ctx, monkeypatch):
    def always(symbol, bars):
        return Lean(symbol, bars[-1].date, "buy", 0.9, "momentum")

    monkeypatch.setattr(replay_account, "BASELINES", {"momentum": ("Momentum: always buys.", always)})
    make_study(ctx, {})
    momentum = lines(replay_report(ctx).account)["momentum"]
    assert momentum.caption == "Momentum: always buys." and momentum.trades == 2
