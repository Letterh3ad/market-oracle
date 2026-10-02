"""Paper ledgers credit cash dividends on the pay date, for the shares held at the close before the
ex-date, never earlier."""

from datetime import date, timedelta

import pytest

from oracle.broker.dividends import assumed_lag
from oracle.market.dividends import Dividend
from oracle.services.paper_service import (
    CORE_SAT, Sleeve, account_score, account_view, credit_dividends, dividend_credits, fill_pending, place_buy, place_sell,
)
from oracle.services.prices_service import update_listing
from oracle.store.dividends import DividendRepo
from oracle.store.paper import PaperRepo
from tests.fakes.yahoo import bar
from tests.services.test_paper import clock, ctx, next_day, ohlc, yahoo  # noqa: F401

EX, PAY = date(2026, 9, 23), date(2026, 9, 25)


async def hold_12_aapl(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 1000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    assert account_view(ctx, "manual").positions[0].shares == 12


async def advance_to(ctx, yahoo, clock, day: date):
    for n in range(1, (day - ctx.clock.utcnow().date()).days + 1):
        d = date.fromordinal(ctx.clock.utcnow().date().toordinal() + 1)
        if d.weekday() < 5:
            yahoo.series["AAPL"].bars.append(ohlc(d.isoformat(), 100.0, 100.0))
            yahoo.series["GBPUSD=X"].bars.append(bar(d.isoformat(), 1.25))
        clock.advance(86400)
    await update_listing(ctx, "AAPL")
    from oracle.services.fx_service import update_fx
    await update_fx(ctx)


def dividend(ctx, yahoo, ex=EX, pay=PAY, amount=0.5):
    """Listed by the fake Yahoo too, so a refetch keeps it (a fetch is the truth for its window)."""
    yahoo.dividends.setdefault("AAPL", []).append((ex, amount))
    DividendRepo(ctx.conn).upsert("AAPL", [Dividend(ex, amount, "USD")], "yahoo", "t")
    if pay:
        DividendRepo(ctx.conn).set_pay_dates("AAPL", {ex: pay})


async def test_a_known_dividend_is_credited_on_its_pay_date_for_the_shares_held_before_the_ex_date(ctx, yahoo, clock):
    await hold_12_aapl(ctx, yahoo, clock)
    dividend(ctx, yahoo)
    before = account_view(ctx, "manual").cash
    await advance_to(ctx, yahoo, clock, EX)
    assert credit_dividends(ctx) == [], "on the ex-date it is owed, not paid"
    await advance_to(ctx, yahoo, clock, PAY)
    [c] = credit_dividends(ctx)
    assert (c.account, c.symbol, c.ex_date, c.pay_date, c.pay_known, c.shares, c.amount) == (
        "manual", "AAPL", EX, PAY, True, 12, 0.5)
    assert c.cash == pytest.approx(12 * 0.5 * (1 - 0.002) / 1.25), "converted at the pay date's rate, FX spread paid"
    assert account_view(ctx, "manual").cash == pytest.approx(before + c.cash)
    assert credit_dividends(ctx) == [], "once only"
    assert dividend_credits(ctx, "manual") == [c], "in the ledger's history"
    assert account_score(ctx, "manual").dividends == pytest.approx(c.cash)


async def test_shares_bought_on_the_ex_date_earn_nothing_and_shares_sold_on_it_still_do(ctx, yahoo, clock):
    dividend(ctx, yahoo, ex=date(2026, 9, 22), pay=date(2026, 9, 24))
    place_buy(ctx, "AAPL", 1000.0)
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)  # filled at the 22nd's open: the ex-date
    await advance_to(ctx, yahoo, clock, date(2026, 9, 24))
    assert credit_dividends(ctx) == []

    dividend(ctx, yahoo, ex=date(2026, 9, 25), pay=date(2026, 9, 29))
    place_sell(ctx, "AAPL", None)
    await advance_to(ctx, yahoo, clock, date(2026, 9, 25))
    fill_pending(ctx)  # sold at the 25th's open: the ex-date, after holding through the 24th's close
    assert account_view(ctx, "manual").positions == ()
    await advance_to(ctx, yahoo, clock, date(2026, 9, 29))
    [c] = credit_dividends(ctx)
    assert (c.ex_date, c.shares) == (date(2026, 9, 25), 12)


async def test_without_a_pay_date_it_is_credited_the_assumed_lag_after_the_ex_date(ctx, yahoo, clock):
    await hold_12_aapl(ctx, yahoo, clock)
    dividend(ctx, yahoo, pay=None)
    due = EX + assumed_lag("USD")
    await advance_to(ctx, yahoo, clock, due - timedelta(days=1))
    assert credit_dividends(ctx) == []
    await advance_to(ctx, yahoo, clock, due)
    [c] = credit_dividends(ctx)
    assert (c.pay_date, c.pay_known) == (due, False)


async def test_a_pay_date_before_the_ex_date_never_credits_before_the_ex_date(ctx, yahoo, clock):
    await hold_12_aapl(ctx, yahoo, clock)
    dividend(ctx, yahoo, ex=date(2026, 9, 25), pay=date(2026, 9, 23))
    await advance_to(ctx, yahoo, clock, date(2026, 9, 24))
    assert credit_dividends(ctx) == []
    await advance_to(ctx, yahoo, clock, date(2026, 9, 25))
    [c] = credit_dividends(ctx)
    assert c.pay_date == date(2026, 9, 25)


async def test_the_fill_pass_credits_dividends_before_it_records_equity(ctx, yahoo, clock):
    await hold_12_aapl(ctx, yahoo, clock)
    dividend(ctx, yahoo)
    await advance_to(ctx, yahoo, clock, PAY)
    fill_pending(ctx)
    [c] = dividend_credits(ctx, "manual")
    view = account_view(ctx, "manual")
    from oracle.services.paper_service import equity_history
    assert equity_history(ctx, "manual")[-1][1] == pytest.approx(view.equity)
    assert c.cash > 0


async def test_a_frozen_or_off_account_is_not_credited(ctx, yahoo, clock, monkeypatch):
    await hold_12_aapl(ctx, yahoo, clock)
    dividend(ctx, yahoo)
    await advance_to(ctx, yahoo, clock, PAY)
    monkeypatch.setattr("oracle.services.paper_service.FROZEN", frozenset({"manual"}))
    assert credit_dividends(ctx) == []


async def test_each_sleeve_is_credited_for_its_own_shares_and_cash(ctx, yahoo, clock):
    place_buy(ctx, "AAPL", 1000.0, CORE_SAT, source=CORE_SAT)
    place_buy(ctx, "AAPL", 500.0, CORE_SAT, source="manual-sleeve")
    await next_day(ctx, yahoo, clock)
    fill_pending(ctx)
    dividend(ctx, yahoo)
    await advance_to(ctx, yahoo, clock, PAY)
    assert {c.source: c.shares for c in credit_dividends(ctx)} == {CORE_SAT: 12, "manual-sleeve": 6}
    paid = sum(c.cash for c in dividend_credits(ctx, CORE_SAT) if c.source == "manual-sleeve")
    spent = sum(f.cash for f in PaperRepo(ctx.conn).fills(CORE_SAT, "manual-sleeve"))
    assert account_view(ctx, CORE_SAT, Sleeve("manual-sleeve", 1000.0)).cash == pytest.approx(1000.0 + spent + paid)
