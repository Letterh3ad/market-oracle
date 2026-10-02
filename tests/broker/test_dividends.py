from datetime import date, timedelta

from oracle.broker.dividends import DEFAULT_LAG, DividendBook, Payout, assumed_lag, credits

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
