from datetime import date, timedelta

from oracle.broker.dividends import DEFAULT_LAG, DividendBook, Payout, assumed_lag, credits, owed

EX, PAY = date(2026, 3, 10), date(2026, 3, 20)


def test_owed_for_the_shares_held_at_the_close_before_the_ex_date():
    changes = [(date(2026, 3, 1), 10), (EX, 5), (date(2026, 3, 12), -15)]
    [c] = credits(changes, [Payout(EX, 0.5, "USD", PAY)], PAY)
    assert (c.shares, c.cash) == (10, 5.0), "bought on the ex-date earns nothing; sold after it still earns"


def test_nothing_before_the_pay_day_and_nothing_without_shares():
    p = Payout(EX, 0.5, "USD", PAY)
    assert credits([(date(2026, 3, 1), 10)], [p], PAY - timedelta(days=1)) == []
    assert credits([(EX, 10)], [p], PAY) == []


def test_without_a_pay_date_the_lag_is_by_market_and_never_before_the_ex_date():
    assert Payout(EX, 1.0, "USD").pay_day == EX + timedelta(days=30)
    assert Payout(EX, 1.0, "JPY").pay_day == EX + timedelta(days=95)
    assert assumed_lag("BRL") == timedelta(days=DEFAULT_LAG)
    assert Payout(EX, 1.0, "USD", EX - timedelta(days=3)).pay_day == EX


def test_the_book_pays_each_payout_once_on_its_pay_day():
    book = DividendBook({"A": [Payout(EX, 1.0, "USD", PAY)], "B": []})
    book.fill("A", date(2026, 3, 2), 4)
    assert book.due(date(2026, 3, 19)) == []
    [(symbol, c)] = book.due(date(2026, 3, 23))
    assert (symbol, c.shares, c.cash) == ("A", 4, 4.0)
    assert book.due(date(2026, 3, 24)) == []


def test_owed_from_the_ex_date_until_the_pay_day_then_paid_instead():
    changes, p = [(date(2026, 3, 1), 10)], Payout(EX, 0.5, "USD", PAY)
    assert owed(changes, [p], EX - timedelta(days=1)) == []
    [c] = owed(changes, [p], EX)
    assert (c.shares, c.cash) == (10, 5.0)
    assert owed(changes, [p], PAY - timedelta(days=1)) == [c]
    assert owed(changes, [p], PAY) == [], "paid on the pay day, so no longer owed"
    assert owed([(EX, 10)], [p], EX) == [], "bought on the ex-date: nothing owed"


def test_the_book_owes_from_the_ex_date_and_never_counts_a_payout_twice():
    book = DividendBook({"A": [Payout(EX, 1.0, "USD", PAY)]})
    book.fill("A", date(2026, 3, 2), 4)
    assert book.owed(EX - timedelta(days=1)) == [] and book.due(EX - timedelta(days=1)) == []
    [(symbol, c)] = book.owed(EX)
    assert (symbol, c.cash) == ("A", 4.0)
    assert book.due(EX) == [], "owed is not spendable before the pay day"
    assert len(book.owed(PAY)) == 0 and len(book.due(PAY)) == 1, "paid on its pay day, owed no longer"
    assert book.owed(PAY) == [] and book.due(PAY) == []


def test_the_book_still_owes_a_payout_whose_pay_day_is_past_the_last_day_asked():
    book = DividendBook({"A": [Payout(EX, 1.0, "USD", PAY + timedelta(days=400))]})
    book.fill("A", date(2026, 3, 2), 4)
    assert [c.cash for _, c in book.owed(PAY)] == [4.0]
