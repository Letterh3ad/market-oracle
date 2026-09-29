import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.broker.backtest import Venue, fill_costs
from oracle.broker.fills import COSTS, buy_fill
from oracle.broker.rules import Lean
from oracle.config import Config, RiskConfig, StrategyConfig
from oracle.market.bars import Bar
from oracle.market.fx import FxRate
from oracle.services import replay_account
from oracle.services.context import open_context
from oracle.services import replay_score
from oracle.services.replay_score import replay_report, score_study
from oracle.store.bars import BarRepo
from oracle.store.fx import FxRepo
from oracle.store.universe import UniverseRepo
from oracle.store.meta import MetaRepo
from oracle.store.replay import ReplayRepo
from oracle.universe.listings import Listing
from tests.fakes.clock import FakeClock
from tests.fakes.cutoff import store_probe
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


def store(ctx, symbol, closes, currency="USD"):
    BarRepo(ctx.conn).upsert([Bar(symbol, d, None, None, None, c, None, None, currency, "test")
                              for d, c in closes.items()], "2026-09-23T22:00:00Z")


def make_study(ctx, calls, failed=(), symbols=SYMBOLS, bands=None):
    repo = ReplayRepo(ctx.conn)
    listings = [{"symbol": s, "name": s, "region": "US", "sector": "Energy",
                 "market_cap": (bands or {}).get(s, "Mega Cap")} for s in symbols]
    sid = repo.create(NOW, JUN, AUG, json.dumps(listings), [(s, d) for d in (JUN, JUL, AUG) for s in symbols],
                      allowed=1)
    cells = {(c.symbol, c.as_of): c.id for c in repo.cells(sid)}
    at = datetime(2026, 9, 20, 12, 5, tzinfo=timezone.utc)
    for key, verdict in calls.items():
        repo.mark_done(cells[key], replay_run(ctx, *key, *verdict), at)
    for key in failed:
        repo.mark_failed(cells[key], "model down", at)
    return sid


@contextmanager
def opened(tmp_path, **config):
    with open_context(Config(db_path=tmp_path / "o.db", **config), None, FakeClock(NOW)) as c:
        store(c, "AAA", path(fall_after_july))
        store(c, "BBB", path(rise))
        yield c


@pytest.fixture
def ctx(tmp_path):
    with opened(tmp_path, **CONFIG) as c:
        yield c


@pytest.fixture
def default_ctx(tmp_path):
    with opened(tmp_path) as c:
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
    assert ai.trades == 2 and held.trades == 2 and ai.costs == pytest.approx(held.costs, rel=0.01)

    assert ai.return_ == pytest.approx(held.return_, abs=0.002)  # whole shares
    assert ai.max_fall == pytest.approx(held.max_fall, abs=0.004)
    assert account.start == JUN and account.end == LAST
    assert ("No stored exchange rate to GBP for AAA, BBB: priced as if a share cost 1 GBP on the first day, so "
            "their fees are approximate.") in account.notes


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
    assert [line["name"] for line in out["lines"]] == ["ai", "all_buy", "buy_and_hold", "momentum", "regime"]
    assert out["lines"][0]["return"] == pytest.approx(lines(replay_report(ctx).account)["ai"].return_)
    assert out["lines"][0]["points"][0] == ["2026-06-01", 0.0]


def test_a_baseline_account_runs_through_the_same_engine(ctx, monkeypatch):
    def always(symbol, bars):
        return Lean(symbol, bars[-1].date, "buy", 0.9, "momentum")

    monkeypatch.setattr(replay_account, "BASELINES",
                        {"momentum": lambda history, rules: ("Momentum", "Momentum: always buys.", always)})
    make_study(ctx, {})
    momentum = lines(replay_report(ctx).account)["momentum"]
    assert momentum.caption == "Momentum: always buys." and momentum.trades == 2


def test_the_momentum_baseline_is_drawn_and_leaves_the_other_lines_unchanged(ctx, monkeypatch):
    make_study(ctx, {**all_buy(), ("AAA", JUL): ("sell", 0.8)})
    account = replay_report(ctx).account
    momentum = lines(account)["momentum"]
    assert momentum.label == "Momentum" and "top 10 listings" in momentum.caption and "12-1 momentum" in momentum.caption
    # Both listings rank, and the top 10 of two is both: bought on the first day, as buy and hold is.
    assert momentum.trades == 2 and momentum.points[0] == (JUN, 0.0)
    monkeypatch.setattr(replay_account, "BASELINES", {})
    without = replay_report(ctx).account
    assert without.lines == tuple(x for x in account.lines if x.name != "momentum") and (without.trades, without.notes) == (account.trades, account.notes)


def test_the_control_line_trades_every_verdict_as_a_buy_under_the_default_caps(default_ctx):
    make_study(default_ctx, {**all_buy(), ("AAA", JUL): ("sell", 0.8)})
    account = replay_report(default_ctx).account
    ai, control, held = (lines(account)[n] for n in ("ai", "all_buy", "buy_and_hold"))
    # 10% a position, so both traded lines are mostly cash: the verdicts' effect is AI against the control.
    assert control.trades >= 2 and ai.trades >= 3
    assert abs(control.return_) < abs(held.return_) / 3
    assert ai.return_ > control.return_
    assert any("at most 10 positions of at most 10% each" in n and "20% in cash" in n and "8% stop" in n
               for n in account.notes)


def test_the_control_equals_the_verdicts_when_every_verdict_is_a_buy(default_ctx):
    make_study(default_ctx, all_buy())
    ai, control = (lines(replay_report(default_ctx).account)[n] for n in ("ai", "all_buy"))
    assert (ai.points, ai.trades) == (control.points, control.trades)


def test_the_stop_sells_a_position_the_verdicts_still_hold(tmp_path):
    with opened(tmp_path, **{**CONFIG, "strategy": StrategyConfig(stop_loss=0.08)}) as ctx:
        make_study(ctx, {("AAA", JUN): ("buy", 0.9)})
        sells = [t for t in replay_report(ctx).account.trades if t.side.value == "sell"]
        assert [t.symbol for t in sells] == ["AAA"] and sells[0].reason.startswith("stop:")


def test_a_buy_verdict_that_goes_stale_is_never_bought(tmp_path):
    with opened(tmp_path, **{**CONFIG, "strategy": StrategyConfig(max_positions=1, stop_loss=0.5)}) as ctx:
        # One position: AAA is bought first; BBB's June buy is past its 7 days when AAA is sold in July.
        make_study(ctx, {("AAA", JUN): ("buy", 0.9), ("BBB", JUN): ("buy", 0.8), ("AAA", JUL): ("sell", 0.8)})
        trades = replay_report(ctx).account.trades
        assert [(t.symbol, t.side.value) for t in trades] == [("AAA", "buy"), ("AAA", "sell")]


def test_a_listing_with_older_bars_is_held_flat_after_its_last_close(ctx):
    cut = date(2026, 9, 10)
    ctx.conn.execute("DELETE FROM bars WHERE symbol = 'BBB' AND date > '2026-09-10'")
    # AAA flat after the cut too, so every line is flat there only if BBB keeps its last close.
    aaa = path(fall_after_july)
    store(ctx, "AAA", {d: aaa[cut] if d > cut else c for d, c in aaa.items()})
    make_study(ctx, all_buy())
    account = replay_report(ctx).account
    assert account.listings == SYMBOLS and account.end == LAST and account.left_out == ()
    assert any("BBB" in n and "2026-09-10" in n and "held flat" in n and "refetched about monthly" in n
               for n in account.notes)
    for line in account.lines:
        at_cut = dict(line.points)[cut]
        assert all(r == pytest.approx(at_cut) for d, r in line.points if d > cut), line.name


def test_a_lagging_listing_whose_last_fetch_failed_says_so(ctx):
    ctx.conn.execute("DELETE FROM bars WHERE symbol = 'BBB' AND date > '2026-09-10'")
    sid = make_study(ctx, all_buy())
    MetaRepo(ctx.conn).set(f"replay_prices:{sid}", json.dumps({"failures": ["BBB"]}))
    notes = replay_report(ctx).account.notes
    assert any(n.startswith("BBB: prices only up to 2026-09-10") and "no prices fetched" in n for n in notes)


def test_a_listing_a_whole_fetch_found_nothing_newer_for_has_stopped_and_is_left_out(ctx):
    ctx.conn.execute("DELETE FROM bars WHERE symbol = 'BBB' AND date > '2026-09-10'")
    sid = make_study(ctx, all_buy())
    MetaRepo(ctx.conn).set(f"replay_prices:{sid}", json.dumps({"fetched": {"BBB": "2026-09-20"}}))
    account = replay_report(ctx).account
    assert account.listings == ("AAA",) and account.left_out == ("BBB: no close since 2026-09-10",)


def test_a_listing_whose_replays_all_failed_is_held_by_buy_and_hold_only_and_named(ctx):
    make_study(ctx, {(s, d): v for (s, d), v in all_buy().items() if s == "AAA"},
               failed=[("BBB", d) for d in (JUN, JUL, AUG)])
    account = replay_report(ctx).account
    assert account.listings == SYMBOLS and {t.symbol for t in account.trades} == {"AAA"}
    assert "No verdict for: BBB." in account.notes


def test_a_split_before_the_study_does_not_reach_the_risk_size(ctx):
    closes = path(rise)
    store(ctx, "BBB", {d: c * (2.5 if d < date(2026, 3, 2) else 1) for d, c in closes.items()})
    make_study(ctx, all_buy())
    account = replay_report(ctx).account
    spent = {t.symbol: -t.cash for t in account.trades}
    assert account.listings == SYMBOLS and spent["BBB"] == pytest.approx(spent["AAA"], rel=0.01)


def test_a_split_between_the_first_as_of_day_and_the_next_close_does_not_reach_the_risk_size(ctx):
    closes = path(rise)
    store(ctx, "BBB", {d: c * (2.5 if d <= JUN else 1) for d, c in closes.items()})
    make_study(ctx, {(s, JUL): ("buy", 0.9) for s in SYMBOLS})
    account = replay_report(ctx).account
    # Sizing starts after the jump, so by July BBB has too few bars for a size, never a tiny one.
    assert account.listings == SYMBOLS and {t.symbol for t in account.trades} == {"AAA"}


def test_a_notional_capital_keeps_whole_shares_from_distorting_the_lines(tmp_path):
    # At the default 10,000 GBP a 1,000 GBP position cannot buy a 1,600 GBP share.
    with opened(tmp_path) as ctx:
        FxRepo(ctx.conn, "GBP").upsert([FxRate("USD", JUN, 1.25, "test")], "2026-09-23T22:00:00Z")
        store(ctx, "BBB", {d: c * 20 for d, c in path(rise).items()})
        make_study(ctx, all_buy())
        account = replay_report(ctx).account
        ai, control, held = (lines(account)[n] for n in ("ai", "all_buy", "buy_and_hold"))
        assert account.capital == 1_000_000 and {t.symbol for t in account.trades} == {"AAA", "BBB"}
        assert (ai.points, ai.trades) == (control.points, control.trades)
        closes = {s: {d: c for d, c in path(m).items()} for s, m in (("AAA", fall_after_july), ("BBB", rise))}
        mean = sum(c[LAST] / c[date(2026, 6, 2)] - 1 for c in closes.values()) / 2
        # Fully invested in equal parts, less entry costs of about 0.4%.
        assert held.return_ == pytest.approx(mean - 0.004, abs=0.001) and held.trades == 2
        assert held.costs == pytest.approx(4_000, rel=0.1)
        assert any("Simulated at a notional 1,000,000 GBP so whole shares do not distort the comparison; fixed "
                   "fee minimums are negligible at this size" in n for n in account.notes)


def test_rejections_collapse_to_one_line_per_listing_and_reason():
    notes = replay_account.collapsed([
        "2026-07-02 AAA: not enough cash to buy (1,000.00 asked, 500.00 left)",
        "2026-08-04 AAA: not enough cash to buy (1,200.00 asked, 100.00 left)",
        "2026-07-02 BBB: 3.00 buys no whole share after costs"])
    assert notes == ["Could not fill AAA 2 times from 2026-07-02: not enough cash to buy.",
                     "Could not fill BBB once, on 2026-07-02: buys no whole share after costs."]


def test_fills_pay_stamp_duty_on_lse_buys_and_the_fx_spread_outside_the_base_currency(ctx):
    UniverseRepo(ctx.conn).replace_all([
        Listing("AAA", "AAA Inc", "Equity", "United States", "NMS", currency="USD", company_key="aaa", is_primary=True),
        Listing("LLL.L", "LLL plc", "Equity", "United Kingdom", "LSE", currency="GBp", company_key="lll",
                is_primary=True)])
    FxRepo(ctx.conn, "GBP").upsert([FxRate("USD", date(2025, 5, 1), 1.25, "test")], "2026-09-23T22:00:00Z")
    store(ctx, "LLL.L", {d: c * 100 for d, c in path(rise).items()}, currency="GBp")
    symbols = ("AAA", "LLL.L")
    make_study(ctx, {(s, JUN): ("buy", 0.9) for s in symbols}, symbols=symbols)
    account = replay_report(ctx).account
    trades = {t.symbol: t for t in account.trades}
    for symbol, (country, exchange, rate) in {"AAA": ("United States", "NMS", 1.25),
                                              "LLL.L": ("United Kingdom", "LSE", 1.0)}.items():
        t = trades[symbol]
        fill = buy_fill(-t.cash + 1e-6, open_price=t.price / (1 + COSTS.slippage), rate=rate, country=country,
                        exchange=exchange, costs=COSTS)
        assert fill.shares == t.shares and t.costs == pytest.approx(fill_costs(fill, rate))
        assert (fill.fx > 0) == (symbol == "AAA") and (fill.stamp > 0) == (symbol == "LLL.L")
    assert trades["LLL.L"].currency == "GBP" and trades["LLL.L"].price < 200  # pounds, not pence
    held = lines(account)["buy_and_hold"]
    assert held.costs == pytest.approx(sum(t.costs for t in account.trades), rel=0.05)
    assert lines(account)["ai"].costs == pytest.approx(sum(t.costs for t in account.trades))
    assert any("0.2% FX spread" in n and "0.5% stamp duty on LSE buys" in n for n in account.notes)
    assert not any("No stored exchange rate" in n for n in account.notes)


def test_replays_on_or_before_the_cutoff_are_left_out_of_the_account_as_of_the_score(ctx):
    make_study(ctx, {**all_buy(), ("AAA", JUL): ("sell", 0.8)})
    store_probe(ctx.conn, ctx.config, date(2026, 6, 15))  # a re-probe found the model knows June
    s = replay_report(ctx)
    assert s.excluded == 2
    account = s.account
    # The June buys are gone, so the July sell finds nothing held; both lines start from July.
    assert account.start == JUL
    assert [(t.symbol, t.side.value, t.day) for t in account.trades] == [("BBB", "buy", date(2026, 7, 2)),
                                                                       ("AAA", "buy", date(2026, 8, 4))]
    assert min(d for d, _ in lines(account)["buy_and_hold"].points) == JUL


def test_fills_pay_slippage_by_the_size_band_the_study_picked_the_listing_in(ctx):
    make_study(ctx, {(s, JUN): ("buy", 0.9) for s in SYMBOLS}, bands={"BBB": "Small Cap"})
    account = replay_report(ctx).account
    closes = {s: dict((b.date, b.close) for b in BarRepo(ctx.conn).history(s)) for s in SYMBOLS}
    trades = {t.symbol: t for t in account.trades}
    assert trades["AAA"].price == pytest.approx(closes["AAA"][trades["AAA"].day] * 1.001)
    assert trades["BBB"].price == pytest.approx(closes["BBB"][trades["BBB"].day] * 1.005)
    held = lines(account)["buy_and_hold"]
    assert held.costs == pytest.approx(sum(t.costs for t in account.trades), rel=0.05)
    assert any("slippage by size" in n and "0.50% for Small Cap" in n for n in account.notes)


def test_the_account_is_drawn_for_each_size_as_well_as_overall(ctx):
    make_study(ctx, all_buy(), bands={"BBB": "Small Cap"})
    s = replay_report(ctx)
    assert s.account.listings == SYMBOLS
    large, small = s.by_size
    assert (large.account.listings, small.account.listings) == (("AAA",), ("BBB",))
    assert [line.name for line in small.account.lines] == ["ai", "all_buy", "buy_and_hold", "momentum", "regime"]
    # BBB only rose, AAA fell after July: the smaller half did better.
    assert lines(small.account)["buy_and_hold"].return_ > lines(large.account)["buy_and_hold"].return_
    shaped = s.to_json()["by_size"][1]["account"]["lines"][0]
    assert shaped["return"] == lines(small.account)["ai"].return_


def test_the_slippage_note_lists_bands_biggest_first_by_rate():
    venues = {s: Venue("United States", "NMS", band) for s, band in
              (("A", "Small Cap"), ("B", "Large Cap"), ("C", None), ("D", "Mega Cap"), ("E", "Mid Cap"))}
    assert replay_account._slippage(venues) == ("slippage by size band (0.10% for Mega Cap, Large Cap and no known "
                                                "band; 0.25% for Mid Cap; 0.50% for Small Cap)")


def test_the_regime_line_is_drawn_in_every_size_band_from_one_book(ctx, monkeypatch):
    built = []
    real = replay_account.regime_line

    def counted(*args, **kwargs):
        built.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(replay_score, "regime_line", counted)
    make_study(ctx, all_buy(), bands={"BBB": "Small Cap"})
    s = replay_report(ctx)
    assert len(built) == 1
    for account in (s.account, *(z.account for z in s.by_size)):
        regime = lines(account)["regime"]
        assert regime.label == "Regime" and regime.points[0] == (JUN, 0.0)
        assert "the replay verdicts as its AI input, each from its as-of day for up to 7 days" in regime.caption


def regime_of(tmp_path, name, calls):
    """The regime line of a study over the fixture's listings, in dollars: without a stored
    rate to the base currency a listing is left out of the market's mood."""
    with open_context(Config(db_path=tmp_path / f"{name}.db", base_currency="USD", **CONFIG), None,
                      FakeClock(NOW)) as c:
        store(c, "AAA", path(fall_after_july))
        store(c, "BBB", path(rise))
        make_study(c, calls)
        return lines(replay_report(c).account)["regime"]


def test_the_regime_line_reads_the_replay_verdicts_as_its_ai_input(tmp_path):
    assert replay_account.regime_verdicts({"AAA": [(JUL, "sell", 0.8), (JUN, "buy", 0.9), (AUG, "odd", 0.5)]}) == {
        "AAA": ((JUN, 0.5), (JUL, -0.5))}
    bought = regime_of(tmp_path, "buy", all_buy())
    sold = regime_of(tmp_path, "sell", {key: ("sell", 0.9) for key in all_buy()})
    assert bought.trades >= 1 and bought.points != sold.points


def test_a_later_verdict_never_changes_the_regime_line_before_its_day(tmp_path):
    a = regime_of(tmp_path, "a", all_buy())
    b = regime_of(tmp_path, "b", {**all_buy(), ("AAA", AUG): ("sell", 0.9), ("BBB", AUG): ("sell", 0.9)})
    assert [p for p in a.points if p[0] <= AUG] == [p for p in b.points if p[0] <= AUG]
    assert a.points != b.points
